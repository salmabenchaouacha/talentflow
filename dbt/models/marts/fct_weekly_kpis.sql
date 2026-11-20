-- Indicateurs du tableau de bord hebdomadaire (Metabase / Power BI)
with weeks as (
    select week_start from {{ ref('stg_needs') }}
    union select week_start from {{ ref('stg_documents') }}
    union select week_start from {{ ref('stg_matches') }}
),
needs as (
    select week_start, count(*) as needs_received
    from {{ ref('stg_needs') }} group by 1
),
cvs as (
    select week_start, count(*) as cvs_ingested
    from {{ ref('stg_documents') }} where kind = 'cv' group by 1
),
matches as (
    select
        week_start,
        count(*)                                        as proposals,
        count(*) filter (where is_validated)            as validated,
        count(*) filter (where is_rejected)             as rejected,
        count(*) filter (where status = 'sent')         as sent,
        avg(final_score)                                as avg_final_score,
        avg(judge_score)                                as avg_judge_score,
        percentile_cont(0.5) within group (order by minutes_need_to_proposal)
                                                        as median_minutes_to_proposal,
        percentile_cont(0.5) within group (order by hours_to_review)
                                                        as median_hours_to_review
    from {{ ref('fct_matches') }} group by 1
)
select
    w.week_start,
    coalesce(n.needs_received, 0)                       as needs_received,
    coalesce(c.cvs_ingested, 0)                         as cvs_ingested,
    coalesce(m.proposals, 0)                            as proposals,
    coalesce(m.validated, 0)                            as validated,
    coalesce(m.rejected, 0)                             as rejected,
    coalesce(m.sent, 0)                                 as sent,
    round(m.validated::numeric / nullif(m.validated + m.rejected, 0), 3) as validation_rate,
    round(m.avg_final_score::numeric, 3)                as avg_final_score,
    round(m.avg_judge_score::numeric, 1)                as avg_judge_score,
    m.median_minutes_to_proposal,
    m.median_hours_to_review
from weeks w
left join needs n using (week_start)
left join cvs c using (week_start)
left join matches m using (week_start)
order by w.week_start
