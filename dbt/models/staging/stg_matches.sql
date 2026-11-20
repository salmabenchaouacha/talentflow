select
    match_id,
    need_id,
    candidate_id,
    rank,
    final_score,
    pre_score,
    vector_score,
    skill_coverage,
    judge_score,
    recommendation,
    status,
    status in ('validated', 'sent')              as is_validated,
    status = 'rejected'                          as is_rejected,
    reviewer,
    review_comment,
    created_at::timestamptz                      as created_at,
    reviewed_at::timestamptz                     as reviewed_at,
    sent_at::timestamptz                         as sent_at,
    extract(epoch from (reviewed_at::timestamptz - created_at::timestamptz)) / 3600.0
                                                 as hours_to_review,
    date_trunc('week', created_at::timestamptz)::date as week_start
from {{ source('talentflow', 'matches') }}
