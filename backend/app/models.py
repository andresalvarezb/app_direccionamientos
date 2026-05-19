from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    estado: Mapped[str] = mapped_column(String(30), nullable=False, default="procesando")
    total_filas: Mapped[int] = mapped_column(default=0)
    filas_insertadas: Mapped[int] = mapped_column(default=0)
    filas_rechazadas: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    records = relationship("RegistroActual", back_populates="batch")
    errors = relationship("ImportErrorRow", back_populates="batch")


class RegistroActual(Base):
    """
    Snapshot actual. Cada nueva carga exitosa reemplaza el contenido de esta tabla.
    El histórico completo no se conserva aquí porque el archivo diario es foto completa.
    """

    __tablename__ = "registros_actuales"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)

    id_ciclo_dispensacion: Mapped[str] = mapped_column(String(80), nullable=False)
    eps: Mapped[str | None] = mapped_column(String(180), nullable=True)
    numero_prescripcion: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fecha_maxima_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    tipo_doc_paciente: Mapped[str | None] = mapped_column(String(30), nullable=True)
    identificacion_paciente: Mapped[str] = mapped_column(String(40), nullable=False)
    fecha_direccionamiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    codigo_tecnologia_direccionada: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tecnologia_direccionada: Mapped[str | None] = mapped_column(Text, nullable=True)
    cantidad_total_entregar: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    municipio: Mapped[str | None] = mapped_column(String(120), nullable=True)
    departamento: Mapped[str | None] = mapped_column(String(120), nullable=True)
    domiciliario: Mapped[str | None] = mapped_column(String(80), nullable=True)
    estado_paciente: Mapped[str | None] = mapped_column(String(120), nullable=True)
    estado_final: Mapped[str | None] = mapped_column(String(120), nullable=True)
    laboratorio: Mapped[str | None] = mapped_column(String(180), nullable=True)
    valor_direccionado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    categoria: Mapped[str | None] = mapped_column(String(1), nullable=True)
    copago: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    batch = relationship("ImportBatch", back_populates="records")

    __table_args__ = (
        UniqueConstraint("id_ciclo_dispensacion", name="uq_registro_actual_id_ciclo"),
        Index("idx_registro_actual_documento", "identificacion_paciente"),
        Index("idx_registro_actual_doc_fecha", "identificacion_paciente", "fecha_direccionamiento"),
    )


class ImportErrorRow(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    row_number: Mapped[int] = mapped_column(nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    batch = relationship("ImportBatch", back_populates="errors")
