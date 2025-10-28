from holidays.countries.spain import Spain
from datetime import date

def es_festivo_galicia(fecha: date) -> bool:
    festivos = Spain(years=fecha.year, subdiv='GA')
    return fecha in festivos
