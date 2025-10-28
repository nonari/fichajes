import holidays
from datetime import date

def es_festivo_galicia(fecha: date) -> bool:
    festivos = holidays.Spain(years=fecha.year, subdiv='GA')
    return fecha in festivos
