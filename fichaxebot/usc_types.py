"""Official USC choices shared by API requests and configuration.

Definitions come from resources/ausencias_types.html and employment_category_types.html.
Config uses enum member names; selectors use display_name. Web codes remain available as code.
"""
from enum import Enum


class UscType(Enum):
    @property
    def display_name(self) -> str:
        return self.value['name']

    @property
    def code(self) -> str:
        return self.value['code']

    @classmethod
    def from_config(cls, value, field):
        """Resolve a normalized enum name, reporting valid configuration choices."""
        if isinstance(value, str) and value in cls.__members__:
            return cls[value]
        choices = '; '.join(f'{item.name} ({item.display_name})' for item in cls)
        raise ValueError(f"'{field}' no es válido: {value!r}. Valores válidos: {choices}")

    @classmethod
    def from_code(cls, value, field):
        """Resolve the ID returned by an existing USC API or Mini App catalog."""
        for item in cls:
            if item.code == value:
                return item
        choices = '; '.join(f'{item.code} ({item.display_name})' for item in cls)
        raise ValueError(f"'{field}' no es válido: {value!r}. Valores válidos: {choices}")


class AbsenceType(UscType):
    ASISTENCIA_A_CURSO_DE_FORMACION = {"name": "Asistencia a curso de formación", "code": "3"}
    FALECEMENTO_ACCIDENTE_OU_ENFERMIDADE_DE_FAMILIAR = {"name": "Falecemento, accidente ou enfermidade de familiar", "code": "4"}
    EXAMES_FINAIS_E_PROBAS = {"name": "Exames finais e probas", "code": "5"}
    TRASLADO_DE_DOMICILIO = {"name": "Traslado de domicilio", "code": "6"}
    DESPRAZAMENTOS_AUTORIZADOS = {"name": "Desprazamentos autorizados", "code": "7"}
    DESPRAZAMENTO_COMISION_SERVIZOS_AUTORIZADA = {"name": "Desprazamento comision servizos autorizada", "code": "8"}
    DEBERES_PERSOAIS_INESCUSABLES = {"name": "Deberes persoais inescusables", "code": "9"}
    FUNCIONS_SINDICAIS = {"name": "Funcións sindicais", "code": "10"}
    OUTRAS_AUSENCIAS = {"name": "Outras ausencias", "code": "11"}
    AUSENCIA_RECUPERABLE = {"name": "Ausencia recuperable", "code": "12"}
    COMPENSACION_POR_TRABALLAR_EN_FESTIVOS = {"name": "Compensación por traballar en festivos", "code": "13"}


class EmploymentCategory(UscType):
    CONTRATADOS_DE_CONTRATOS_E_PROXECTOS = {"name": "Contratados de Contratos e Proxectos", "code": "Proxectos"}
    CONTRATADOS_JIN = {"name": "Contratados JIN", "code": "JIN"}
    CONTRATADOS_MARIE_CURIE = {"name": "Contratados Marie Curie", "code": "MARIECURIE"}
    CONTRATADOS_PREDOUTORAIS = {"name": "Contratados predoutorais", "code": "PREDOUTORAIS"}
    CONTRATADOS_POSDOUTORAIS = {"name": "Contratados posdoutorais", "code": "POSDOUTORAIS"}
    INVESTIGADOR_BEATRIZ_GALINDO = {"name": "Investigador Beatriz Galindo", "code": "BeatrizGalindo"}
    INVESTIGADOR_DISTINGUIDO = {"name": "Investigador distinguido", "code": "DISTINGUIEOD"}
    JUAN_DE_LA_CIERVA = {"name": "Juan de la Cierva", "code": "JUANDELACIERVA"}
    RAMON_Y_CAJAL = {"name": "Ramón y Cajal", "code": "RAMONYCAJAL"}
    TECNICOS_DE_APOIO = {"name": "Técnicos de apoio", "code": "TECNICOAPOIO"}
