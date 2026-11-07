{#
    TIMESTAMP_TZ -> TIMESTAMP_NTZ in UTC.

    A plain ::timestamp_ntz cast keeps the wall-clock time and silently drops
    the offset, so the result depends on the session time zone. Converting to
    UTC first makes every timestamp in the warehouse mean the same thing.
#}
{% macro to_utc(column) -%}
    convert_timezone('UTC', {{ column }})::timestamp_ntz
{%- endmacro %}
