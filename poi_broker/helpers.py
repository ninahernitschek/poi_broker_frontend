from sqlalchemy import inspect
import re
import json
from datetime import datetime
from astropy.time import Time

def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}

# Helper for converting SQLAlchemy objects to dictionaries
def object_as_dict(obj):
    return {c.key: getattr(obj, c.key)
            for c in inspect(obj).mapper.column_attrs}

def result_to_dict(query_results):
    def to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}    
    return [to_dict(result) for result in query_results]


# Helper for filtering
def extract_numbers(text):
    #number with optional decimal point
    regex = r"[<>]?[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))"
    matches = re.findall(regex, text)
    if len(matches) < 1:
        return None
    elif len(matches) == 1:
        return [matches[0]] # >/< Are preserved and will be handled in the filter extraction functions
    else:
        return list(map(lambda m: m.replace('>', '').replace('<', ''), matches[0:2])) # >/< are removed since order is determined by natural sorting of the (two) values only

def extract_dates(text) -> list[str]:
    #date in yyyymmdd format, with optional > or < for filtering
    regex = r"[<>]?\d{8}"
    matches = re.findall(regex, text)
    if len(matches) > 0:
        # convert dates in yyyymmdd format to ISO format
        try:
            date_objs = [datetime.strptime(m, "%Y%m%d") for m in matches]
            return [str(d.date().isoformat()) for d in date_objs] # convert to ISO format string without time component
        except ValueError:
            pass

    #date in ISO format with optional time, with optional > or < for filtering
    if len(matches) < 1:
        regex_iso = r"[<>]?\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?"
        matches = re.findall(regex_iso, text)

    if len(matches) < 1:
        return []
    elif len(matches) == 1:
        return [matches[0]]
    else:
        return list(map(lambda m: m.replace('>', '').replace('<', ''), matches[0:2]))

def extract_float_filter(input_field, db_field, query, decimals=0):
    float_func = lambda x: float(x)
    offset = 10**(-decimals) if decimals > 0 else 0.0
    return extract_filter(input_field, db_field, query, float_func, upper_offset=offset/2, lower_offset=offset/2)

def extract_int_filter(input_field, db_field, query):
    int_func = lambda x: int(x)
    return extract_filter(input_field, db_field, query, int_func)

def extract_mjd_filter(input_field, db_field, query):
    mjd_func = lambda x: Time(x, format='iso', scale='utc').mjd
    offset_mjd = 1.0/(3600*24) if re.match(r"[ T]\d{2}:\d{2}:\d{2}", input_field[0]) else 1.0+1.0/(3600*24) # if time component is missing, use a larger offset to account for the entire day, otherwise we only add a second to account for potential rounding errors in the MJD conversion of the input date
    return extract_filter(input_field, db_field, query, mjd_func, upper_offset=offset_mjd)

def extract_filter(input_field, db_field, query, convert_callback, upper_offset=0.0, lower_offset=0.0):
    #pdb.set_trace()
    if len(input_field) == 1:
        if '>' in input_field[0]:
            query = query.filter(db_field >= convert_callback(input_field[0].replace('>', '')) - lower_offset)
        elif '<' in input_field[0]:
            query = query.filter(db_field <= convert_callback(input_field[0].replace('<', '')) + upper_offset)
        else:
            # exact match for one value with precission loss
            lower_bound = convert_callback(input_field[0]) - lower_offset # We go from 0 to 999 millisec for exact dates resp. 0:00 to 23:59 for date inputs without time component, so we need to add an offset to the upper bound to account for this potential loss of precision. For date inputs with time component, we only add a small offset to account for potential rounding errors in the MJD conversion.
            upper_bound = convert_callback(input_field[0]) + upper_offset
            if lower_bound == upper_bound:
                query = query.filter(db_field == lower_bound)
            else:
                # range to account for rounding errors of seconds and whole days in case of date inputs without time component
                query = query.filter(db_field >= lower_bound)
                query = query.filter(db_field <= upper_bound)
            # MJD time can suffer from rounding errors: DB: 61056.12116899993 vs calc: 61056.12116898148
    else: #2 inputs
        filter_fields = [convert_callback(x) for x in input_field]
        filter_fields.sort()  #NOTE: Ensure >min <max order is correct regardless of input order
        lower_bound = filter_fields[0] - lower_offset
        upper_bound = filter_fields[1] + upper_offset
        query = query.filter(db_field >= lower_bound)
        query = query.filter(db_field <= upper_bound)
    return query

# Helper function for safe serialization
def safe_serialize(obj):
    """Safely serialize a dictionary to JSON."""
    try:
        return json.dumps(obj)
    except TypeError as e:
        print(f"Serialization error: {e}")
        return json.dumps(serialize_fallback(obj))
   
def serialize_fallback(obj):
    """Fallback handler to make the object serializable by converting binary data to string."""
    if isinstance(obj, bytes):
        return obj.decode('utf-8')  # Convert binary to string
    elif isinstance(obj, dict):
        return {k: serialize_fallback(v) for k, v in obj.items()}  # Keep dict as-is and process its values
    elif isinstance(obj, list):
        return [serialize_fallback(v) for v in obj]  # Keep list as-is and process its elements
    else:
        return obj  # Return other types as-is
