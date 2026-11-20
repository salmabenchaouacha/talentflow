select
    document_id,
    kind,
    source,
    parser,
    pii_counts::jsonb                            as pii_counts,
    received_at::timestamptz                     as received_at,
    date_trunc('week', received_at::timestamptz)::date as week_start
from {{ source('talentflow', 'documents') }}
