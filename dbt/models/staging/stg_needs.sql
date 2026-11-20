select
    need_id,
    client,
    title,
    status,
    need_json ->> 'seniority'                         as seniority,
    (need_json ->> 'min_years')::numeric               as min_years,
    need_json ->> 'location'                          as location,
    need_json ->> 'remote_policy'                     as remote_policy,
    (need_json ->> 'max_daily_rate')::numeric          as max_daily_rate,
    need_json -> 'required_skills'                    as required_skills,
    need_json -> 'nice_to_have'                       as nice_to_have,
    received_at::timestamptz                     as received_at,
    matched_at::timestamptz                      as matched_at,
    date_trunc('week', received_at::timestamptz)::date as week_start
from (select *, need::jsonb as need_json from {{ source('talentflow', 'needs') }}) src
