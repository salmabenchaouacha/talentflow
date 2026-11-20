-- Une ligne par proposition, enrichie du besoin et du profil (sans donnée identifiante)
select
    m.*,
    n.client,
    n.title            as need_title,
    n.seniority        as need_seniority,
    n.received_at      as need_received_at,
    extract(epoch from (m.created_at - n.received_at)) / 60.0 as minutes_need_to_proposal,
    c.title            as candidate_title,
    c.seniority        as candidate_seniority,
    c.years_experience
from {{ ref('stg_matches') }} m
join {{ ref('stg_needs') }} n using (need_id)
join {{ ref('stg_candidates') }} c using (candidate_id)
