# DropZone SF

Elastic × Mistral SF Hack Night, 3 Nov 2026.

**Find a safe drone passenger-pickup zone near any SF address.** Passenger drones take trips off congested streets, but every pickup needs open ground near the rider, big enough for the craft, with no people camping or obstacles nearby. DropZone SF picks that spot from SF open data and tells the rider where to walk, in their language.

## How it works

| Step | Tech |
|---|---|
| Rider's request in any language ("Two of us need a ride from Turk and Jones, one suitcase") | **Mistral structured outputs** → JSON: passengers, luggage, wheelchair, urgency, language. Drives the query: min zone size = 0.5 ac + 0.25 per extra passenger + 0.1 per bag; wheelchair or medical → max 300 m walk |
| Orchestration | **Mistral function calling** agent (`mistral-large`) decides which Elastic tools to call |
| Geocode the drop-off | **Elasticsearch** `multi_match` over park names + 311 addresses |
| Candidate landing zones: all 255 Rec & Park properties | **Elasticsearch** `geo_point` + `geo_shape`, `geo_distance` filter, `_geo_distance` sort |
| Hazards around each zone: open 311 hazard reports within 150 m, the worst ones ranked by meaning ("unsafe to land") | `geo_distance` + `terms` filters and a `terms` aggregation by kind in **Elasticsearch**; ranking by **Mistral embeddings** (`mistral-embed`) through an Elasticsearch inference endpoint (`semantic_text`, `semantic` query) |
| Score = size − hazards − 10 × encampments − walk distance; pick, runner-up, walking directions in the rider's language | **Mistral chat**, grounded in the tool results only |

Data: [Rec & Park properties](https://data.sf.gov/d/gtr9-ntp6) and ~7k recent [311 cases](https://data.sf.gov/d/vw6y-z8j6).

## Run

```bash
pip install -r requirements.txt
cat > .env <<'X'
ES_URL=https://<project>.es.<region>.elastic.cloud
ES_API_KEY=...
MISTRAL_API_KEY=...
X
python droplz.py setup        # Mistral inference endpoint + droplz_zones + fixit_311 (semantic_text)
python droplz.py ask "Two of us need a drone ride from Turk and Jones in the Tenderloin, we have one suitcase"
python droplz.py ask "Necesito un dron para mi abuela en silla de ruedas, estamos en Turk y Jones"
```

Already loaded the starter notebooks (`sf_parks`, `sf_311_cases`)? Skip setup and point at them;
hazards then match by 311 category instead of Mistral semantic search:

```bash
ZONES_INDEX=sf_parks HAZARDS_INDEX=sf_311_cases python droplz.py ask "..."
```

`fixit311.py` is the shared 311 ingest (and a bonus: free-text 311 triage in any language).

## 2-minute pitch

1. **Who (15 s):** Passenger drones are coming to SF. The hard part isn't flying. It's *where to pick people up*: open ground, near the rider, big enough, and no one on the ground underneath.
2. **Demo (60 s):** "Two of us need a drone ride from Turk and Jones, one suitcase."
   - Mistral **structured outputs** turn that into JSON: 2 passengers, 1 bag, so at least 0.85 acres.
   - The Mistral **agent calls Elastic**. The nearest park, Boeddeker (230 m), drops to the bottom: 12 open hazards, 3 of them encampments. Mistral picks the 5.4-acre Civic Center plaza (500 m, no encampments).
   - Then in Spanish, for a grandmother in a wheelchair: the walk limit drops to 300 m, and it answers in Spanish with honest encampment counts.
3. **Elastic (20 s):** 255 parks as geo points and shapes. Geo-distance filtering and sorting within walking distance. Per zone, geo filters and aggregations count open 311 hazards within 150 m by type, and a semantic query ranks the worst ones.
4. **Mistral (20 s):** structured outputs turn a messy request into query parameters. A function-calling agent orchestrates the Elastic queries. Mistral embeddings run inside Elasticsearch through an inference endpoint. Replies are multilingual and grounded only in the data.
5. **Next (5 s):** Voxtral voice requests, vision check of a photo from the rider's spot, and street trees and power lines as obstacles.
