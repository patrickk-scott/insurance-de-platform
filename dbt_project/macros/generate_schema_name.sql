{#
    Use the folder's +schema name as is: STAGING, INTERMEDIATE, MARTS, ACTUARIAL.

    dbt's default glues it onto the target schema (STAGING_MARTS,
    STAGING_ACTUARIAL), which exists so several developers can share one
    database without overwriting each other. Here each target already has
    its own database (INSURANCE_PLATFORM, _CI, _PROD), so the prefix only adds
    noise. Models without a +schema fall back to the target schema.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
