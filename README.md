# DropZone SF

Elastic × Mistral SF Hack Night, 3 Nov 2026.

**Find a safe drone-delivery landing zone near any SF address.** Drones take food delivery off congested streets, but they need somewhere to land: open, big enough, close to the customer, and free of people, encampments, debris and scooters. DropZone SF picks that spot from SF open data.

## How it works

| Step | Tech |
|---|---|
| Plain-English request ("Drop a burrito for someone at 24th & Valencia") | **Mistral function calling** agent (`mistral-large`) decides which tools to call |
| Geocode the drop-off | **Elasticsearch** `multi_match` over park names + 311 addresses |
| Candidate landing zones: all 255 Rec & Park properties | **Elasticsearch** `geo_point` + `geo_shape`, `geo_distance` filter, `_geo_distance` sort |
| Hazards around each zone: open 311 reports *meaning* "unsafe to land" | **Mistral embeddings** (`mistral-embed`) as an Elasticsearch inference endpoint → `semantic_text` + `semantic` query + 75 m `geo_distance` filter + `terms` aggregation by kind |
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
python droplz.py setup
python droplz.py ask "Drop a burrito order for someone at 24th St and Valencia"
```

`fixit311.py` is the shared 311 ingest (and a bonus: free-text 311 triage in any language).

## 2-minute pitch

1. **Who (15 s):** SF delivery is stuck in traffic. Drones fix that, but every drone delivery needs a safe place to come down near the customer. Today nobody knows where that is.
2. **Demo (60 s):** type a delivery request. Show Mistral calling Elastic (`lookup_place`, then `find_landing_zones`), the ranked zones with hazard counts, and the final pick and runner-up.
3. **Elastic (20 s):** 255 parks as geo points and shapes. A geo-distance sort finds zones within walking distance. Per zone, a semantic search for "unsafe to land" over open 311 reports within 75 m, with aggregations by hazard type.
4. **Mistral (20 s):** a function-calling agent orchestrates the queries. Mistral embeddings run inside Elasticsearch through an inference endpoint, so "people sleeping" matches Encampment reports with no keyword lists. Mistral explains the choice in the customer's language.
5. **Next (5 s):** add street trees and power lines as obstacles, plus live 311. Later, passenger pickup.
