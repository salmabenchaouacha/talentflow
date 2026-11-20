-- Veille : compétences demandées par les clients vs consultants disponibles sous 60 jours
with demand as (
    select skill, count(*) as times_required
    from {{ ref('stg_needs') }}, jsonb_array_elements_text(required_skills) as skill
    group by 1
),
pool as (
    select s ->> 'name' as skill, count(distinct candidate_id) as available_consultants
    from {{ ref('stg_candidates') }}, jsonb_array_elements(skills) as s
    where availability_date is null or availability_date <= current_date + 60
    group by 1
)
select
    d.skill,
    d.times_required,
    coalesce(p.available_consultants, 0) as available_consultants,
    case
        when coalesce(p.available_consultants, 0) = 0 then 'à sourcer en priorité'
        when p.available_consultants < d.times_required then 'à sourcer'
        else 'couvert'
    end as action
from demand d
left join pool p using (skill)
order by d.times_required desc
