# FixIt 311

Elastic × Mistral SF Hack Night, 3 Nov 2026.

**Who it helps:** any SF resident who sees a street problem, in any language, and doesn't know the 311 category or whether it's already been reported.

**What it does:** you describe the problem in your own words. FixIt 311:
1. **Mistral (structured outputs):** turns free text in any language into a clean 311 report: category (constrained to the real 311 categories, read from an Elastic aggregation), English summary, address, urgency, language.
2. **Elastic (geocoding):** finds the address by matching it against addresses already in the 311 index.
3. **Elastic (semantic + geo):** a `semantic_text` field embedded by **Mistral embeddings** through an Elasticsearch inference endpoint, searched with a `geo_distance` filter, finds similar *open* cases within 400 m, so you can add to an existing case instead of filing a duplicate.
4. **Elastic (aggregations):** median hours-to-close for that category, citywide vs. your neighborhood.
5. **Mistral (chat):** answers back in your language.

Data: ~7k recent [SF 311 cases](https://data.sf.gov/d/vw6y-z8j6), up to 350 per request type over the last 21 days.

## Run

```bash
pip install elasticsearch mistralai requests
cat > .env <<'X'
ES_URL=https://<project>.es.<region>.elastic.cloud
ES_API_KEY=...
MISTRAL_API_KEY=...
X
python fixit311.py setup     # Mistral inference endpoint, index, ingest
python fixit311.py ask "Hay un hoyo enorme en la calle frente al 2800 de Mission St, casi me caigo con la bici"
```
