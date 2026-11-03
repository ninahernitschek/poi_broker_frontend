import pytest

import poi_broker.models as models_module
from poi_broker import db
from poi_broker.models import Classification, Ztf
from poi_broker.querybuilder_translator import Filter, TableNotFoundError


def test_filter_module_init_scans_models(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter(models_module, base_query)

        assert 'featuretable' in filter_obj.models
        assert 'classification' in filter_obj.models


def test_querybuilder_with_empty_rules_returns_query(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)

        filtered_query = filter_obj.querybuilder({'rules': []})
        assert filtered_query.whereclause is None


def test_querybuilder_unsupported_operator_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.alert_id', 'operator': 'not_a_real_op', 'value': 'x'}]}

        with pytest.raises(ValueError, match='Unsupported operator'):
            filter_obj.querybuilder(rules)


def test_querybuilder_invalid_field_format_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'alert_id', 'operator': 'equal', 'value': 'x'}]}

        with pytest.raises(ValueError, match='Invalid field format'):
            filter_obj.querybuilder(rules)


def test_querybuilder_unknown_table_name_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'unknown.alert_id', 'operator': 'equal', 'value': 'x'}]}

        with pytest.raises(TableNotFoundError):
            filter_obj.querybuilder(rules)


def test_querybuilder_non_dict_rule_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': ['not-a-rule']}

        with pytest.raises(ValueError, match='Invalid querybuilder rule'):
            filter_obj.querybuilder(rules)


def test_querybuilder_non_string_field_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': ['featuretable', 'alert_id'], 'operator': 'is_not_null'}]}

        with pytest.raises(ValueError, match='Invalid field format'):
            filter_obj.querybuilder(rules)


def test_querybuilder_unknown_column_name_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.no_column', 'operator': 'equal', 'value': 'x'}]}

        with pytest.raises(ValueError, match='Unknown field'):
            filter_obj.querybuilder(rules)


def test_querybuilder_between_invalid_payload_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.date_alert_mjd', 'operator': 'between', 'value': [1]}]}

        with pytest.raises(ValueError, match='Operator "between" requires a two-item array value'):
            filter_obj.querybuilder(rules)


def test_querybuilder_in_invalid_payload_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.alert_id', 'operator': 'in', 'value': []}]}

        with pytest.raises(ValueError, match='Operator "in" requires a non-empty array value'):
            filter_obj.querybuilder(rules)


def test_querybuilder_missing_value_raises(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.alert_id', 'operator': 'equal', 'value': None}]}

        with pytest.raises(ValueError, match='requires a value'):
            filter_obj.querybuilder(rules)


def test_querybuilder_unary_operator_is_null(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {'rules': [{'field': 'featuretable.ztf_object_id', 'operator': 'is_null'}]}

        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert 'IS NULL' in sql.upper()


def test_querybuilder_add_entity_and_nested_rules(app):
    with app.app_context():
        base_query = db.session.query(Ztf, Classification).outerjoin(
            Classification, Ztf.alert_id == Classification.alert_id
        )
        filter_obj = Filter({'featuretable': Ztf, 'classification': Classification}, base_query)
        rules = {
            'condition': 'AND',
            'rules': [
                {'field': 'featuretable.locus_ra', 'operator': 'greater', 'value': 1},
                {
                    'condition': 'OR',
                    'rules': [
                        {'field': 'featuretable.locus_dec', 'operator': 'less', 'value': 2},
                        {'field': 'classification.p_cvnova', 'operator': 'greater_or_equal', 'value': 0.5},
                    ],
                },
            ],
        }

        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert 'featuretable.locus_ra > 1' in sql
        assert 'featuretable.locus_dec < 2' in sql
        assert 'classification.p_cvnova >= 0.5' in sql
        assert ' OR ' in sql.upper()


def test_querybuilder_rejects_classification_without_join(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf, 'classification': Classification}, base_query)
        rules = {
            'rules': [
                {'field': 'classification.prob_class', 'operator': 'equal', 'value': 'sn'},
            ]
        }
        with pytest.raises(ValueError, match='not in the query'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_contains_on_indexed_id(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'contains',
                    'value': 'ztf',
                }
            ]
        }
        with pytest.raises(ValueError, match='not allowed on indexed ID'):
            filter_obj.querybuilder(rules)


def test_querybuilder_begins_with_is_prefix_like(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'begins_with',
                    'value': 'ztf_candidate:',
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        compiled = filtered_query.whereclause.compile()
        sql = str(compiled)
        params = compiled.params
        assert 'LIKE' in sql.upper()
        assert 'ESCAPE' in sql.upper()
        # Underscore in the value is escaped so it is literal, not a LIKE wildcard.
        assert any(v == 'ztf\\_candidate:%' for v in params.values())


def test_querybuilder_contains_escapes_like_metacharacters(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.anomaly_type',
                    'operator': 'contains',
                    'value': '100%',
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        compiled = filtered_query.whereclause.compile()
        params = compiled.params
        assert any(v == '%100\\%%' for v in params.values())


def test_querybuilder_contains_still_works_for_anomaly_fields(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.anomaly_mask',
                    'operator': 'contains',
                    'value': 'bad',
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        compiled = filtered_query.whereclause.compile()
        params = compiled.params
        assert any(v == '%bad%' for v in params.values())


def test_querybuilder_rejects_non_column_attribute(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [{'field': 'featuretable.classification', 'operator': 'is_null'}]
        }
        with pytest.raises(ValueError, match='Unknown field'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_non_string_condition(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'condition': 1,
            'rules': [{'field': 'featuretable.alert_id', 'operator': 'is_not_null'}],
        }
        with pytest.raises(ValueError, match='Query condition must be AND or OR'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_unknown_condition_string(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'condition': 'XOR',
            'rules': [{'field': 'featuretable.alert_id', 'operator': 'is_not_null'}],
        }
        with pytest.raises(ValueError, match='Query condition must be AND or OR'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_nested_non_string_condition(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'condition': 'AND',
            'rules': [
                {
                    'condition': None,
                    'rules': [{'field': 'featuretable.alert_id', 'operator': 'is_not_null'}],
                }
            ],
        }
        with pytest.raises(ValueError, match='Query condition must be AND or OR'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_dict_equal_value(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'equal',
                    'value': {'a': 1},
                }
            ]
        }
        with pytest.raises(ValueError, match='Invalid value for operator "equal"'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_dict_in_elements(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'in',
                    'value': [{}],
                }
            ]
        }
        with pytest.raises(ValueError, match='Invalid value for operator "in"'):
            filter_obj.querybuilder(rules)


def test_querybuilder_rejects_dict_between_elements(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.locus_ra',
                    'operator': 'between',
                    'value': [{}, {}],
                }
            ]
        }
        with pytest.raises(ValueError, match='Invalid value for operator "between"'):
            filter_obj.querybuilder(rules)


def test_querybuilder_in_with_array_builds_where_clause(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'in',
                    'value': ['a', 'b'],
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert 'IN' in sql.upper()
        assert "'a'" in sql
        assert "'b'" in sql


def test_querybuilder_equal_unwraps_single_element_array(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.ant_passband',
                    'operator': 'equal',
                    'value': ['g'],
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert "ant_passband = 'g'" in sql


def test_querybuilder_equal_rejects_multi_element_array(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'equal',
                    'value': ['a', 'b'],
                }
            ]
        }
        with pytest.raises(ValueError, match='Invalid value for operator "equal"'):
            filter_obj.querybuilder(rules)


def test_querybuilder_equal_multi_error_guides_toward_in(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.ant_passband',
                    'operator': 'equal',
                    'value': ['g', 'R'],
                }
            ]
        }
        with pytest.raises(ValueError, match='use "in" for multiple values'):
            filter_obj.querybuilder(rules)


def test_querybuilder_equal_rejects_dict_in_single_element_array(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'equal',
                    'value': [{}],
                }
            ]
        }
        with pytest.raises(ValueError, match='Invalid value for operator "equal"'):
            filter_obj.querybuilder(rules)


def test_querybuilder_in_rejects_scalar_value(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'in',
                    'value': 'a,b',
                }
            ]
        }
        with pytest.raises(ValueError, match='requires a non-empty array value'):
            filter_obj.querybuilder(rules)


def test_querybuilder_in_rejects_empty_array(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.alert_id',
                    'operator': 'in',
                    'value': [],
                }
            ]
        }
        with pytest.raises(ValueError, match='requires a non-empty array value'):
            filter_obj.querybuilder(rules)


def test_querybuilder_contains_unwraps_single_element_array(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.anomaly_type',
                    'operator': 'contains',
                    'value': ['a'],
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert 'LIKE' in sql.upper()
        assert 'a' in sql


def test_querybuilder_contains_joins_multi_element_array_with_comma(app):
    with app.app_context():
        base_query = db.session.query(Ztf)
        filter_obj = Filter({'featuretable': Ztf}, base_query)
        rules = {
            'rules': [
                {
                    'field': 'featuretable.anomaly_type',
                    'operator': 'contains',
                    'value': ['a', 'b'],
                }
            ]
        }
        filtered_query = filter_obj.querybuilder(rules)
        sql = str(filtered_query.whereclause.compile(compile_kwargs={'literal_binds': True}))

        assert 'LIKE' in sql.upper()
        assert 'a,b' in sql
