import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ImportBatch, ImportErrorRow, RegistroActual
from .normalization import (
    clean_document,
    clean_text,
    file_hash,
    map_dataframe_columns,
    parse_date,
    parse_decimal,
    row_hash,
    stable_json,
)

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


def read_uploaded_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object)
    if suffix == ".csv":
        return pd.read_csv(path, dtype=object, sep=None, engine="python", encoding="utf-8-sig")
    raise HTTPException(status_code=400, detail="Formato no soportado. Usa .xlsx, .xls o .csv")


def dataframe_row_to_raw(row: pd.Series) -> str:
    raw: dict[str, Any] = {}
    for key, value in row.to_dict().items():
        if pd.isna(value):
            raw[str(key)] = None
        else:
            raw[str(key)] = value
    return stable_json(raw)


def build_record(row: pd.Series) -> tuple[dict[str, Any] | None, str | None]:
    id_ciclo = clean_text(row.get("id_ciclo_dispensacion"), upper=True)
    documento = clean_document(row.get("identificacion_paciente"))

    if not id_ciclo:
        return None, "ID Ciclo Dispensación vacío"
    if not documento:
        return None, "Identificación del Paciente vacía"

    data: dict[str, Any] = {
        "id_ciclo_dispensacion": id_ciclo,
        "eps": clean_text(row.get("eps"), upper=True),
        "numero_prescripcion": clean_text(row.get("numero_prescripcion"), upper=True),
        "fecha_maxima_entrega": parse_date(row.get("fecha_maxima_entrega")),
        "tipo_doc_paciente": clean_text(row.get("tipo_doc_paciente"), upper=True),
        "identificacion_paciente": documento,
        "fecha_direccionamiento": parse_date(row.get("fecha_direccionamiento")),
        "codigo_tecnologia_direccionada": clean_text(row.get("codigo_tecnologia_direccionada"), upper=True),
        "tecnologia_direccionada": clean_text(row.get("tecnologia_direccionada"), upper=True),
        "cantidad_total_entregar": parse_decimal(row.get("cantidad_total_entregar")),
        "nombre": clean_text(row.get("nombre"), upper=True),
        "municipio": clean_text(row.get("municipio"), upper=True),
        "departamento": clean_text(row.get("departamento"), upper=True),
        "domiciliario": clean_text(row.get("domiciliario"), upper=True),
        "estado_paciente": clean_text(row.get("estado_paciente"), upper=True),
        "estado_final": clean_text(row.get("estado_final"), upper=True),
        "laboratorio": clean_text(row.get("laboratorio"), upper=True),
        "valor_direccionado": parse_decimal(row.get("valor_direccionado")),
        "categoria": None,
        "copago": None,
    }
    data["raw_json"] = dataframe_row_to_raw(row)
    data["row_hash"] = row_hash(data)
    return data, None


async def import_file(db: Session, upload: UploadFile) -> ImportBatch:
    original_name = upload.filename or "archivo_sin_nombre"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa .xlsx, .xls o .csv")

    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío")

    digest = file_hash(content)
    exists = db.scalar(select(ImportBatch).where(ImportBatch.file_hash == digest))
    if exists:
        raise HTTPException(
            status_code=409,
            detail=f"Este archivo ya fue cargado en el batch #{exists.id}. No se permite duplicar la misma foto diaria.",
        )

    stored_path = UPLOAD_DIR / f"{digest}{suffix}"
    stored_path.write_bytes(content)

    batch = ImportBatch(filename=original_name, file_hash=digest, estado="procesando")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    try:
        df = read_uploaded_file(stored_path)
        df, missing = map_dataframe_columns(df)

        batch.total_filas = int(len(df.index))

        if missing:
            batch.estado = "fallido"
            batch.error = "Faltan columnas obligatorias: " + ", ".join(missing)
            db.commit()
            raise HTTPException(status_code=400, detail=batch.error)

        records_to_insert: list[RegistroActual] = []
        errors_to_insert: list[ImportErrorRow] = []
        seen_ids: set[str] = set()

        for idx, row in df.iterrows():
            excel_row_number = int(idx) + 2  # fila 1 suele ser encabezado
            record_data, error = build_record(row)

            if error:
                errors_to_insert.append(
                    ImportErrorRow(
                        import_batch_id=batch.id,
                        row_number=excel_row_number,
                        error=error,
                        raw_json=dataframe_row_to_raw(row),
                    )
                )
                continue

            assert record_data is not None
            id_ciclo = record_data["id_ciclo_dispensacion"]
            if id_ciclo in seen_ids:
                errors_to_insert.append(
                    ImportErrorRow(
                        import_batch_id=batch.id,
                        row_number=excel_row_number,
                        error=f"ID Ciclo Dispensación duplicado dentro del archivo: {id_ciclo}",
                        raw_json=dataframe_row_to_raw(row),
                    )
                )
                continue

            seen_ids.add(id_ciclo)
            records_to_insert.append(RegistroActual(import_batch_id=batch.id, **record_data))

        if not records_to_insert:
            batch.estado = "fallido"
            batch.filas_rechazadas = len(errors_to_insert)
            batch.error = "No se encontró ninguna fila válida para insertar"
            db.add_all(errors_to_insert)
            db.commit()
            raise HTTPException(status_code=400, detail=batch.error)

        # CASO 2: foto completa del estado actual. La nueva carga reemplaza el snapshot completo.
        db.execute(delete(RegistroActual))
        db.add_all(records_to_insert)
        if errors_to_insert:
            db.add_all(errors_to_insert)

        batch.filas_insertadas = len(records_to_insert)
        batch.filas_rechazadas = len(errors_to_insert)
        batch.estado = "completado_con_errores" if errors_to_insert else "completado"
        db.commit()
        db.refresh(batch)
        return batch

    except HTTPException:
        raise
    except Exception as exc:
        batch.estado = "fallido"
        batch.error = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Error procesando archivo: {exc}") from exc
