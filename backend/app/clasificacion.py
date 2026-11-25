"""Lógica de umbrales por edad (Sección III-E del informe).

Los valores son de DEMOSTRACIÓN y deben ser validados por personal de
salud pediátrica (ver Fleming et al., The Lancet 2011).
"""

SPO2_MIN = 92        # bajo este valor: Crítica
SPO2_ATENCION = 95   # entre 92 y 94: Atención
MARGEN_ATENCION = 20 # lpm por debajo del máximo para pasar a Atención
LECTURAS_PARA_ALERTA = 2   # lecturas críticas seguidas antes de avisar
ENFRIAMIENTO_S = 20        # segundos mínimos entre avisos


def fc_max(edad: int) -> int:
    """Frecuencia cardíaca máxima de alerta según la edad (años)."""
    if edad <= 2:
        return 160
    if edad <= 5:
        return 150
    if edad <= 12:
        return 140
    return 130


def estado_fc(fc: float, limite: int) -> str:
    if fc > limite:
        return "critica"
    if fc > limite - MARGEN_ATENCION:
        return "atencion"
    return "normal"


def estado_spo2(spo2: float | None) -> str | None:
    if spo2 is None:
        return None
    if spo2 < SPO2_MIN:
        return "critica"
    if spo2 < SPO2_ATENCION:
        return "atencion"
    return "normal"
