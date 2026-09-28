from html.parser import HTMLParser
from pathlib import Path
import unittest

from fichaxebot.usc_types import AbsenceType, EmploymentCategory


class Options(HTMLParser):
    def __init__(self, fixture):
        super().__init__()
        self.values = {}
        self.option = None
        self.feed((Path(__file__).parent / 'fixtures' / fixture).read_text(encoding='utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'radio':
            self.values[attrs['value']] = attrs['data-label']
        if tag == 'option':
            self.option = attrs.get('value')

    def handle_endtag(self, tag):
        if tag == 'option':
            self.option = None

    def handle_data(self, value):
        if self.option:
            self.values[self.option] = value.strip()


class UscTypesTests(unittest.TestCase):
    def test_enum_choices_resolve_to_supplied_usc_names_and_codes(self):
        for enum, fixture, count in (
                (AbsenceType, 'absence_types.html', 11),
                (EmploymentCategory, 'employment_category_types.html', 10)):
            options = Options(fixture).values
            self.assertEqual(len(options), count)
            self.assertEqual({item.code: item.display_name for item in enum}, options)
            for item in enum:
                with self.subTest(enum=enum.__name__, member=item.name):
                    self.assertEqual(enum.from_config(item.name, 'setting').display_name, options[item.code])
                    self.assertIs(enum.from_code(item.code, 'id'), item)

    def test_normalized_members_have_requested_record_format(self):
        for enum, name, record in (
                (AbsenceType, 'DESPRAZAMENTOS_AUTORIZADOS',
                 {'name': 'Desprazamentos autorizados', 'code': '7'}),
                (EmploymentCategory, 'CONTRATADOS_DE_CONTRATOS_E_PROXECTOS',
                 {'name': 'Contratados de Contratos e Proxectos', 'code': 'Proxectos'})):
            self.assertEqual(enum.from_config(name, 'setting').value, record)
            for value in (record['name'], record['code'], name.lower()):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    enum.from_config(value, 'setting')

    def test_invalid_values_raise_value_error_with_normalized_choices(self):
        for enum, choice in ((AbsenceType, 'DESPRAZAMENTOS_AUTORIZADOS'),
                             (EmploymentCategory, 'CONTRATADOS_DE_CONTRATOS_E_PROXECTOS')):
            for value in (None, '', [], {}, True, 'unknown'):
                with self.subTest(enum=enum.__name__, value=value), self.assertRaises(ValueError) as error:
                    enum.from_config(value, 'setting')
                self.assertIn('setting', str(error.exception))
                self.assertIn(repr(value), str(error.exception))
                self.assertIn(choice, str(error.exception))
