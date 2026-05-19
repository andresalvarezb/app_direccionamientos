from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import ImportBatch, ImportErrorRow, RegistroActual
from .schemas import (
    CategoriaUpdateIn,
    CategoriaUpdateOut,
    ImportBatchOut,
    ImportErrorOut,
    PacienteInfo,
    RegistrosSearchOut,
)
from .services.import_service import import_file
from .services.normalization import CATEGORY_RATES, calculate_copago, clean_document, clean_text

app = FastAPI(title="App Direccionamientos", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "direccionamientos"}


@app.post("/api/imports/upload", response_model=ImportBatchOut)
async def upload_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await import_file(db, file)


@app.get("/api/imports", response_model=list[ImportBatchOut])
def list_imports(db: Session = Depends(get_db)):
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(50)
    return list(db.scalars(stmt).all())


@app.get("/api/imports/{batch_id}/errors", response_model=list[ImportErrorOut])
def list_import_errors(batch_id: int, db: Session = Depends(get_db)):
    stmt = (
        select(ImportErrorRow)
        .where(ImportErrorRow.import_batch_id == batch_id)
        .order_by(ImportErrorRow.row_number.asc())
    )
    return list(db.scalars(stmt).all())


def build_patient_info(records: list[RegistroActual]) -> PacienteInfo:
    if not records:
        return PacienteInfo()

    first = records[0]
    categorias = {r.categoria for r in records if r.categoria}
    categoria_actual = list(categorias)[0] if len(categorias) == 1 else None

    return PacienteInfo(
        identificacion_paciente=first.identificacion_paciente,
        tipo_doc_paciente=first.tipo_doc_paciente,
        nombre=first.nombre,
        eps=first.eps,
        municipio=first.municipio,
        departamento=first.departamento,
        categoria_actual=categoria_actual,
    )


@app.get("/api/registros", response_model=RegistrosSearchOut)
def search_records(
    query: str = Query(..., min_length=1),
    tipo: str = Query("auto", pattern="^(auto|documento|id_ciclo)$"),
    db: Session = Depends(get_db),
):
    q_doc = clean_document(query)
    q_text = clean_text(query, upper=True)

    if tipo == "documento":
        if not q_doc:
            raise HTTPException(status_code=400, detail="Documento inválido")
        stmt = select(RegistroActual).where(RegistroActual.identificacion_paciente == q_doc)
    elif tipo == "id_ciclo":
        if not q_text:
            raise HTTPException(status_code=400, detail="ID Ciclo inválido")
        stmt = select(RegistroActual).where(RegistroActual.id_ciclo_dispensacion == q_text)
    else:
        conditions = []
        if q_doc:
            conditions.append(RegistroActual.identificacion_paciente == q_doc)
        if q_text:
            conditions.append(RegistroActual.id_ciclo_dispensacion == q_text)
        stmt = select(RegistroActual).where(or_(*conditions)) if conditions else select(RegistroActual).where(False)

    stmt = stmt.order_by(RegistroActual.fecha_direccionamiento.desc().nullslast(), RegistroActual.id.desc())
    records = list(db.scalars(stmt).all())

    total_valor = sum((r.valor_direccionado or Decimal("0")) for r in records)
    total_copago = sum((r.copago or Decimal("0")) for r in records)

    return RegistrosSearchOut(
        query=query,
        tipo_busqueda=tipo,
        total=len(records),
        total_valor_direccionado=total_valor,
        total_copago=total_copago,
        paciente=build_patient_info(records),
        registros=records,
    )


@app.put("/api/registros/categoria", response_model=CategoriaUpdateOut)
def update_categoria(payload: CategoriaUpdateIn, db: Session = Depends(get_db)):
    categoria = payload.categoria.upper()
    rate = CATEGORY_RATES[categoria]

    stmt = select(RegistroActual)

    if payload.identificacion_paciente:
        documento = clean_document(payload.identificacion_paciente)
        if not documento:
            raise HTTPException(status_code=400, detail="Identificación del Paciente inválida")
        stmt = stmt.where(RegistroActual.identificacion_paciente == documento)
    elif payload.id_ciclo_dispensacion:
        id_ciclo = clean_text(payload.id_ciclo_dispensacion, upper=True)
        if not id_ciclo:
            raise HTTPException(status_code=400, detail="ID Ciclo Dispensación inválido")
        stmt = stmt.where(RegistroActual.id_ciclo_dispensacion == id_ciclo)
    else:
        raise HTTPException(
            status_code=400,
            detail="Debes enviar identificacion_paciente o id_ciclo_dispensacion",
        )

    records = list(db.scalars(stmt).all())
    if not records:
        raise HTTPException(status_code=404, detail="No se encontraron registros para actualizar")

    for record in records:
        record.categoria = categoria
        record.copago = calculate_copago(record.valor_direccionado, categoria)

    db.commit()

    return CategoriaUpdateOut(actualizados=len(records), categoria=categoria, porcentaje=rate * Decimal("100"))
