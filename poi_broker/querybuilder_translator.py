import types
from inspect import signature

from sqlalchemy import and_, or_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import DeclarativeMeta


class TableNotFoundError(Exception):
    pass


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters so user input cannot widen the pattern."""
    return (
        str(value)
        .replace('\\', '\\\\')
        .replace('%', '\\%')
        .replace('_', '\\_')
    )


def _like(field, pattern: str):
    return field.like(pattern, escape='\\')


def _notlike(field, pattern: str):
    return field.notlike(pattern, escape='\\')


# Indexed ID columns: contains/ends_with cannot use B-tree indexes (LIKE '%x%').
# Keep in sync with ID_OPERATORS in templates/_query_builder_js.html.
INDEXED_ID_FIELDS = frozenset(
    {
        'featuretable.alert_id',
        'featuretable.locus_id',
        'featuretable.ztf_object_id',
        'featuretable.brightest_alert_id_ztf',
        'classification.alert_id',
    }
)
INDEXED_ID_OPERATORS = frozenset(
    {
        'equal',
        'not_equal',
        'in',
        'not_in',
        'begins_with',
        'not_begins_with',
        'is_empty',
        'is_not_empty',
        'is_null',
        'is_not_null',
    }
)

_GROUP_CONDITIONS = frozenset({'AND', 'OR'})

# Upper bound on in / not_in list length. SQLite supports up to 32766 bind
# parameters (SQLITE_MAX_VARIABLE_NUMBER since 3.32). Real multi-selects are
# far below this; the 1000 limit bounds preview-SQL size and prevents excessive
# bind expansion.
MAX_IN_LIST_ITEMS = 1000


def _group_condition(rules: dict) -> str:
    """Return AND/OR from a rules group. Non-strings and unknown values are errors."""
    condition = rules.get('condition', 'AND')
    if not isinstance(condition, str) or condition.upper() not in _GROUP_CONDITIONS:
        raise ValueError('Query condition must be AND or OR')
    return condition.upper()


def _assert_bind_scalar(value, operator_name: str) -> None:
    """Reject dict/list binds that sqlite cannot render or execute."""
    if isinstance(value, (dict, list, tuple)):
        raise ValueError(f'Invalid value for operator "{operator_name}"') # noqa: TRY004 - routes map ValueError -> 400


# LIKE-family operators take a single pattern string. The query-builder UI
# splits comma input into arrays when a filter allows in/not_in
# (value_separator), so LIKE rules can arrive as arrays. Join them back with
# ',' to preserve the pre-separator literal (e.g. contains "a,b" matches the
# literal "a,b") instead of 400ing. Each element is still scalar-validated.
_LIKE_JOIN_OPERATORS = frozenset({
    'contains',
    'not_contains',
    'begins_with',
    'not_begins_with',
    'ends_with',
    'not_ends_with',
})


OPERATORS = {
    'equal': lambda f, a: f.__eq__(a),
    'not_equal': lambda f, a: f.__ne__(a),
    'less': lambda f, a: f.__lt__(a),
    'greater': lambda f, a: f.__gt__(a),
    'less_or_equal': lambda f, a: f.__le__(a),
    'greater_or_equal': lambda f, a: f.__ge__(a),
    'in': lambda f, a: f.in_(a),
    'not_in': lambda f, a: f.notin_(a),
    'ends_with': lambda f, a: _like(f, '%' + escape_like(a)),
    'begins_with': lambda f, a: _like(f, escape_like(a) + '%'),
    'contains': lambda f, a: _like(f, '%' + escape_like(a) + '%'),
    'not_contains': lambda f, a: _notlike(f, '%' + escape_like(a) + '%'),
    'not_begins_with': lambda f, a: _notlike(f, escape_like(a) + '%'),
    'not_ends_with': lambda f, a: _notlike(f, '%' + escape_like(a)),
    'is_empty': lambda f: f.__eq__(''),
    'is_not_empty': lambda f: f.__ne__(''),
    'is_null': lambda f: f.is_(None),
    'is_not_null': lambda f: f.is_not(None),
    'between': lambda f, a: f.between(a[0], a[1]),
}


class Filter(object):
    """Translate jQuery QueryBuilder rules to SQLAlchemy ORM filters."""

    def __init__(self, models, query, operators=None):
        if isinstance(models, types.ModuleType):
            model_dict = {}
            for attr in models.__dict__.values():
                if isinstance(attr, DeclarativeMeta):
                    try:
                        inspected = sa_inspect(attr)
                        table_name = getattr(inspected.persist_selectable, 'name', None)
                        if table_name:
                            model_dict[table_name] = attr
                    except NoInspectionAvailable:
                        pass
            self.models = model_dict
        else:
            self.models = dict(models)

        self.query = query
        self.operators = operators if operators else OPERATORS

    def querybuilder(self, rules):
        query, cond_list = self._make_query(self.query, rules)
        if not cond_list:
            return query

        condition = _group_condition(rules)
        operator = or_ if condition == 'OR' else and_
        return query.filter(operator(*cond_list))

    def _make_query(self, query, rules):
        cond_list = []
        for cond in rules.get('rules', []):
            if not isinstance(cond, dict):
                raise ValueError('Invalid querybuilder rule') # noqa: TRY004 - routes map ValueError -> 400
            if 'condition' not in cond:
                operator_name = cond.get('operator')
                if not isinstance(operator_name, str) or operator_name not in self.operators:
                    raise ValueError(f'Unsupported operator: {operator_name}')

                field_name = cond.get('field', '')
                if not isinstance(field_name, str):
                    raise ValueError(f'Invalid field format: {field_name}')
                parts = field_name.split('.', 1)
                if len(parts) != 2:
                    raise ValueError(f'Invalid field format: {field_name}')

                if (
                    field_name in INDEXED_ID_FIELDS
                    and operator_name not in INDEXED_ID_OPERATORS
                ):
                    raise ValueError(
                        f'Operator "{operator_name}" is not allowed on indexed ID '
                        f'field {field_name}; use equal, in, or begins_with'
                    )

                table_name, column_name = parts
                try:
                    model = self.models[table_name]
                except KeyError:
                    raise TableNotFoundError(table_name)

                for table in query.column_descriptions:
                    if table.get('entity') == model:
                        break
                else:
                    raise ValueError(
                        f'Table {table_name} is not in the query; outerjoin it '
                        f'before filtering on {field_name}'
                    )

                try:
                    inspected = sa_inspect(model)
                    if column_name not in inspected.columns:
                        raise ValueError(f'Unknown field: {field_name}')
                    field = getattr(model, column_name)
                except AttributeError as exc:
                    raise ValueError(f'Unknown field: {field_name}') from exc

                function = self.operators[operator_name]
                arity = len(signature(function).parameters)
                if arity == 1:
                    cond_list.append(function(field))
                elif arity == 2:
                    value = cond.get('value')

                    # Validate known multi-value operators so malformed payloads
                    # become client errors (ValueError) instead of server errors.
                    if operator_name == 'between':
                        if not isinstance(value, (list, tuple)) or len(value) != 2:
                            raise ValueError('Operator "between" requires a two-item array value')
                        for item in value:
                            _assert_bind_scalar(item, operator_name)
                    elif operator_name in ('in', 'not_in'):
                        if not isinstance(value, (list, tuple)) or len(value) == 0:
                            raise ValueError(f'Operator "{operator_name}" requires a non-empty array value')
                        if len(value) > MAX_IN_LIST_ITEMS:
                            raise ValueError(
                                f'Operator "{operator_name}" accepts at most '
                                f'{MAX_IN_LIST_ITEMS} values'
                            )
                        for item in value:
                            _assert_bind_scalar(item, operator_name)
                    else:
                        # Select filters use multiple:true (required for in/not_in),
                        # so they always emit an array even for single-value
                        # operators. Unwrap a single selection so scalar ops keep
                        # working; multi-selections stay a 400 (except LIKE ops,
                        # which rejoin with ',' to preserve literal comma input).
                        if isinstance(value, (list, tuple)):
                            if operator_name in _LIKE_JOIN_OPERATORS:
                                if len(value) == 0:
                                    raise ValueError(f'Operator "{operator_name}" requires a value')
                                for item in value:
                                    _assert_bind_scalar(item, operator_name)
                                value = ','.join(str(v) for v in value)
                            elif len(value) == 1:
                                value = value[0]
                            elif len(value) > 1:
                                # Multi-select UI (multiple:true) can emit an
                                # array for single-value operators. Point the
                                # caller at "in" instead of a bare 400.
                                raise ValueError(f'Invalid value for operator "{operator_name}": requires a single value; use "in" for multiple values')
                        if value is None:
                            raise ValueError(f'Operator "{operator_name}" requires a value')
                        _assert_bind_scalar(value, operator_name)

                    try:
                        cond_list.append(function(field, value))
                    except (TypeError, ValueError, IndexError) as exc:
                        raise ValueError(f'Invalid value for operator "{operator_name}"') from exc
                else:
                    raise NotImplementedError(f'Unsupported operator arity: {arity}')
            else:
                query, cond_subrule = self._make_query(query, cond)
                nested_condition = _group_condition(cond)
                nested_operator = or_ if nested_condition == 'OR' else and_
                if cond_subrule:
                    cond_list.append(nested_operator(*cond_subrule))

        return query, cond_list
