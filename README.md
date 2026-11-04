# DropZone SF

Elastic × Mistral SF Hack Night, 3 Nov 2026.

**Find a safe drone-delivery landing zone near any SF address.** Drones take food delivery off congested streets, but they need somewhere to land: open, big enough, close to the customer, and free of people, encampments, debris and scooters. DropZone SF picks that spot from SF open data.

## How it works

| Step | Tech |
|---|---|
| Plain-English request ("Drop a burrito for someone at 24th & Valencia") | **Mistral function calling** agent (`mistral-large`) decides which tools to call |
| Geocode the drop-off | **Elasticsearch** `multi_match` over park names + 311 addresses |
| Candidate landing zones: all 255 Rec & Park properties | **Elasticsearch** `geo_point` + `geo_shape`, `geo_distance` filter, `_geo_distance` sort |
| Hazards around each zone: open 311 hazard reports within 150 m, the worst ones ranked by meaning ("unsafe to land") | `geo_distance` + `terms` filters and a `terms` aggregation by kind in **Elasticsearch**; ranking by **Mistral embeddings** (`mistral-embed`) through an Elasticsearch inference endpoint (`semantic_text`, `semantic` query) |
| Score = size − hazards − walk distance; pick + explain, in the user's language | **Mistral chat** |

Data: [Rec & Park properties](https://data.sf.gov/d/gtr9-ntp6) and ~7k recent [311 cases](https://data.sf.gov/d/vw6y-z8j6).

## Run

```bash
pip install elasticsearch mistralai requests
cat > .env <<'X'
ES_URL=https://<project>.es.<region>.elastic.cloud
ES_API_KEY=...
MISTRAL_API_KEY=...
X
python droplz.py setup        # Mistral inference endpoint + droplz_zones + fixit_311 (semantic_text)
python droplz.py ask "Deliver two bowls of pho to someone at Turk St and Jones St in the Tenderloin"
```

Already loaded the starter notebooks (`sf_parks`, `sf_311_cases`)? Skip setup and point at them;
hazards then match by 311 category instead of Mistral semantic search:

```bash
ZONES_INDEX=sf_parks HAZARDS_INDEX=sf_311_cases python droplz.py ask "..."
```

`fixit311.py` is the shared 311 ingest (and a bonus: free-text 311 triage in any language).

## 2-minute pitch

1. **Who (15 s):** SF delivery is stuck in traffic. Drones fix that, but every drone delivery needs a safe place to come down near the customer. Today nobody knows where that is.
2. **Demo (60 s):** "Deliver two bowls of pho to Turk & Jones in the Tenderloin." Mistral calls Elastic. The closest park, Boeddeker (105 m), is **rejected**: 12 open hazards, 3 of them encampments. Mistral picks the 5.4-acre Civic Center plaza (625 m, no encampments) and explains why.
3. **Elastic (20 s):** 255 parks as geo points and shapes. A geo-distance sort finds zones within walking distance. Per zone, geo filters and aggregations count open 311 hazards within 150 m by type, and a semantic query ranks the worst ones.
4. **Mistral (20 s):** a function-calling agent orchestrates the queries. Mistral embeddings run inside Elasticsearch through an inference endpoint, so the agent sees the reports that *mean* "unsafe to land" first. Mistral explains the choice in the customer's language.
5. **Next (5 s):** add street trees and power lines as obstacles, plus live 311. Later, passenger pickup.
