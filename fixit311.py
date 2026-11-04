"""FixIt 311: describe an SF street problem in your own words, in any language.

Mistral turns it into a structured 311 report. Elasticsearch finds the same
problem already reported nearby (semantic + geo), shows how fast the city
usually fixes it there, and Mistral answers back in your language.

  python fixit311.py setup                 # inference endpoint + index + ingest
  python fixit311.py ask "texto libre..."  # triage one report
"""
import json, os, sys, time
from datetime import datetime, timedelta

import requests
from elasticsearch import Elasticsearch, helpers
from mistralai.client import Mistral

# ---------- config (.env next to this file, never committed) ----------
for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')):
    if '=' in line:
        k, v = line.strip().split('=', 1)
        os.environ.setdefault(k, v)

MISTRAL_KEY = os.environ['MISTRAL_API_KEY']
INDEX = 'fixit_311'
EMBED_ID = 'mistral-embeddings'
CHAT_MODEL = 'mistral-large-latest'
RADIUS = os.environ.get('RADIUS', '400m')
DAYS = 21
PER_TYPE = 350

mistral = Mistral(api_key=MISTRAL_KEY)


def es_client():
    return Elasticsearch(os.environ['ES_URL'], api_key=os.environ['ES_API_KEY'], request_timeout=120)


# ---------- setup: Mistral inference endpoint + index + data ----------
def fetch_cases():
    url = 'https://data.sf.gov/resource/vw6y-z8j6.json'
    since = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%dT00:00:00')
    types = requests.get(url, params={
        '$select': 'service_name,count(*) as n', '$group': 'service_name',
        '$where': f"requested_datetime > '{since}'", '$order': 'n DESC', '$limit': 30}, timeout=60).json()
    rows = []
    for t in types:
        name = t['service_name'].replace("'", "''")
        batch = requests.get(url, params={
            '$where': f"requested_datetime > '{since}' AND service_name = '{name}'",
            '$order': 'requested_datetime DESC', '$limit': PER_TYPE}, timeout=120).json()
        rows += batch
        print(f'  {t["service_name"]:45s} {len(batch)}')
    return rows


def to_doc(r):
    def dt(v):
        try:
            return datetime.fromisoformat(v)
        except (TypeError, ValueError):
            return None
    try:
        loc = {'lat': float(r['lat']), 'lon': float(r['long'])}
        if not loc['lat'] or not loc['lon']:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    opened, closed = dt(r.get('requested_datetime')), dt(r.get('closed_date'))
    text = ' | '.join(x for x in [r.get('service_name'), r.get('service_subtype'),
                                  r.get('service_details'), r.get('status_notes')] if x)
    doc = {
        'id': r.get('service_request_id'),
        'requested_at': r.get('requested_datetime'),
        'hours_to_close': round((closed - opened).total_seconds() / 3600, 1) if opened and closed else None,
        'status': r.get('status_description'),
        'service_name': r.get('service_name'),
        'service_subtype': r.get('service_subtype'),
        'address': r.get('address'),
        'neighborhood': r.get('analysis_neighborhood'),
        'location': loc,
        'text': text,
    }
    return {k: v for k, v in doc.items() if v not in (None, '')}


def setup():
    es = es_client()
    print('Elasticsearch', es.info()['version']['number'])
    try:
        es.inference.put(task_type='text_embedding', inference_id=EMBED_ID, inference_config={
            'service': 'mistral', 'service_settings': {'api_key': MISTRAL_KEY, 'model': 'mistral-embed'}})
        print('created inference endpoint', EMBED_ID)
    except Exception as e:
        print('inference endpoint:', str(e)[:120])
    if es.indices.exists(index=INDEX):
        es.indices.delete(index=INDEX)
    es.indices.create(index=INDEX, mappings={'properties': {
        'id': {'type': 'keyword'}, 'requested_at': {'type': 'date'},
        'hours_to_close': {'type': 'float'}, 'status': {'type': 'keyword'},
        'service_name': {'type': 'keyword'}, 'service_subtype': {'type': 'keyword'},
        'address': {'type': 'text'}, 'neighborhood': {'type': 'keyword'},
        'location': {'type': 'geo_point'},
        'text': {'type': 'semantic_text', 'inference_id': EMBED_ID},  # Mistral embeds at index time
    }})
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cases.json')
    if not os.path.exists(cache):
        json.dump(fetch_cases(), open(cache, 'w'))
    docs = [d for d in map(to_doc, json.load(open(cache))) if d]
    print(f'indexing {len(docs)} cases (Mistral embeds each one)...')
    t = time.time()
    ok, errs = helpers.bulk(es, ({'_index': INDEX, '_source': d} for d in docs),
                            chunk_size=100, raise_on_error=False, request_timeout=300)
    print(f'indexed {ok}, errors {len(errs)}, {time.time() - t:.0f}s')
    if errs:
        print(json.dumps(errs[0])[:400])


# ---------- ask: triage one free-text report ----------
def categories(es):
    r = es.search(index=INDEX, size=0, aggs={'t': {'terms': {'field': 'service_name', 'size': 40}}})
    return [b['key'] for b in r['aggregations']['t']['buckets']]


def triage(text, cats):
    """Mistral structured output: free text (any language) -> clean 311 report."""
    schema = {
        'type': 'object', 'additionalProperties': False,
        'required': ['service_name', 'summary_en', 'search_query', 'location_text', 'language', 'urgency'],
        'properties': {
            'service_name': {'type': 'string', 'enum': cats},
            'summary_en': {'type': 'string', 'description': 'one-line English 311 description'},
            'search_query': {'type': 'string', 'description': 'short English phrase to find similar reports'},
            'location_text': {'type': 'string', 'description': 'street address or intersection mentioned, else empty'},
            'language': {'type': 'string', 'description': 'language the resident wrote in'},
            'urgency': {'type': 'string', 'enum': ['low', 'medium', 'high', 'safety_hazard']},
        }}
    r = mistral.chat.complete(model=CHAT_MODEL, temperature=0, messages=[
        {'role': 'system', 'content': 'You triage San Francisco 311 reports written by residents.'},
        {'role': 'user', 'content': text}],
        response_format={'type': 'json_schema', 'json_schema': {'name': 'report', 'schema': schema, 'strict': True}})
    return json.loads(r.choices[0].message.content)


def locate(es, location_text):
    """Geocode with Elasticsearch itself: the closest-matching address already in the 311 index."""
    if not location_text:
        return None, None
    r = es.search(index=INDEX, size=1, query={'match': {'address': location_text}},
                  source=['address', 'location', 'neighborhood'])
    if not r['hits']['hits']:
        return None, None
    s = r['hits']['hits'][0]['_source']
    return s['location'], s


def similar_nearby(es, rep, loc):
    """Semantic search (Mistral embeddings) filtered to open cases within RADIUS."""
    filters = [{'term': {'status': 'Open'}}]
    if loc:
        filters.append({'geo_distance': {'distance': RADIUS, 'location': loc}})
    body = {'size': 5, 'query': {'bool': {
        'must': {'semantic': {'field': 'text', 'query': rep['search_query']}},
        'filter': filters}},
        '_source': ['id', 'service_name', 'service_subtype', 'address', 'requested_at', 'text']}
    return [h['_source'] | {'score': round(h['_score'], 3)} for h in es.search(index=INDEX, **body)['hits']['hits']]


def stats(es, rep, place):
    """Aggregations: how fast this kind of request gets closed here vs citywide."""
    def median(filters):
        r = es.search(index=INDEX, size=0, query={'bool': {'filter': filters}}, aggs={
            'm': {'percentiles': {'field': 'hours_to_close', 'percents': [50]}}})
        return r['hits']['total']['value'], r['aggregations']['m']['values']['50.0']
    base = [{'term': {'service_name': rep['service_name']}}, {'exists': {'field': 'hours_to_close'}}]
    out = {'citywide': median(base)}
    if place and place.get('neighborhood'):
        out[place['neighborhood']] = median(base + [{'term': {'neighborhood': place['neighborhood']}}])
    return out


def answer(text, rep, place, hits, st):
    ctx = json.dumps({'report': rep, 'matched_location': place, 'similar_open_cases_nearby': hits,
                      'closed_case_count_and_median_hours_to_close': st}, default=str)
    r = mistral.chat.complete(model=CHAT_MODEL, messages=[
        {'role': 'system', 'content':
            'You help a San Francisco resident file a 311 report. Reply in the language they wrote in, '
            'in at most 5 short lines: the 311 category to use, whether it looks already reported nearby '
            '(cite case ids and addresses; suggest adding to that case instead of filing a duplicate), and '
            'how long the city usually takes to fix it there. Use only the data given.'},
        {'role': 'user', 'content': f'Resident wrote: {text}\n\nData: {ctx}'}])
    return r.choices[0].message.content


def ask(text):
    es = es_client()
    rep = triage(text, categories(es))
    print('\n[Mistral structured output]\n' + json.dumps(rep, indent=2, ensure_ascii=False))
    loc, place = locate(es, rep['location_text'])
    print(f'\n[Elastic geocode] {place["address"] if place else "no location found"}'
          + (f' ({place["neighborhood"]})' if place and place.get('neighborhood') else ''))
    hits = similar_nearby(es, rep, loc)
    print(f'\n[Elastic semantic + geo] open cases within {RADIUS} like "{rep["search_query"]}":')
    for h in hits:
        print(f'  #{h["id"]}  {h["requested_at"][:10]}  {h["service_name"]} / {h.get("service_subtype", "")}'
              f'  @ {h.get("address", "?")}  score {h["score"]}')
    st = stats(es, rep, place)
    print('\n[Elastic aggregations] median hours to close ' + rep['service_name'] + ':')
    for k, (n, m) in st.items():
        print(f'  {k:25s} {m:6.1f} h  (n={n})' if m else f'  {k:25s}   n/a  (n={n})')
    print('\n[Mistral reply]\n' + answer(text, rep, place, hits, st))


if __name__ == '__main__':
    if sys.argv[1:2] == ['setup']:
        setup()
    elif sys.argv[1:2] == ['ask']:
        ask(' '.join(sys.argv[2:]))
    else:
        print(__doc__)
