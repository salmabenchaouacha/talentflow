select
    candidate_id,
    title,
    seniority,
    years_experience,
    location,
    nullif(availability_date, '')::date          as availability_date,
    daily_rate,
    profile::jsonb -> 'skills'                          as skills,
    extractor,
    created_at::timestamptz                      as created_at,
    retention_until::date                        as retention_until
from {{ source('talentflow', 'candidates') }}
