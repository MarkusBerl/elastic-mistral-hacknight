"""DropZone SF: find a safe drone-delivery landing zone near any SF address.

Candidate zones: every Rec & Park property (DataSF gtr9-ntp6), as geo_point + geo_shape.
Hazards: recent 311 reports (encampments, blocked sidewalks, debris, trees, scooters...)
embedded by Mistral through an Elasticsearch inference endpoint (semantic_text).
A Mistral function-calling agent turns a plain-English delivery request into
Elasticsearch geo + semantic queries, then picks and explains the landing zone.

  python droplz.py setup
  python droplz.py ask "Drop a burrito order for someone at 24th St and Valencia"
"""
import json, math, os, sys

import requests
from elasticsearch import helpers

import fixit311 as base  # loads .env, Mistral client, ES client, 311 cache/ingest

# Works with our own indices (python droplz.py setup) or the starter notebooks' indices:
#   ZONES_INDEX=sf_parks HAZARDS_INDEX=sf_311_cases python droplz.py ask "..."
ZONES = os.environ.get('ZONES_INDEX', 'droplz_zones')
HAZARDS = os.environ.get('HAZARDS_INDEX', base.INDEX)   # fixit_311: 311 cases with Mistral semantic_text
HAZARD_TYPES = ['Encampment', 'Blocked Street and Sidewalk', 'Street and Sidewalk Cleaning', 'Tree Maintenance',
                'Streetlights', 'Street Defect', 'Damage Property', 'Noise', 'Sidewalk and Curb']
HAZARD_QUERY = ('people camping or sleeping, encampment, blocked sidewalk, debris or garbage pile, '
                'abandoned scooters or bikes, low tree branches, fallen tree, broken streetlight, unsafe area')
CHAT = base.CHAT_MODEL
es = None


# ---------- setup ----------
def setup():
    global es
    es = base.es_client()
    if not es.indices.exists(index=HAZARDS):
        base.setup()  # creates Mistral inference endpoint + ingests 311 hazards
    rows = requests.get('https://data.sf.gov/resource/gtr9-ntp6.json', params={'$limit': 1000}, timeout=60).json()
    if es.indices.exists(index=ZONES):
        es.indices.delete(index=ZONES)
    es.indices.create(index=ZONES, mappings={'properties': {
        'name': {'type': 'text', 'fields': {'kw': {'type': 'keyword'}}},
        'type': {'type': 'keyword'}, 'acres': {'type': 'float'},
        'neighborhood': {'type': 'keyword'}, 'address': {'type': 'text'},
        'location': {'type': 'geo_point'}, 'shape': {'type': 'geo_shape'}}})
    docs = []
    for r in rows:
        try:
            loc = {'lat': float(r['latitude']), 'lon': float(r['longitude'])}
        except (KeyError, ValueError):
            continue
        d = {'name': r.get('property_name'), 'type': r.get('propertytype'),
             'acres': float(r.get('acres') or 0), 'neighborhood': r.get('analysis_neighborhood'),
             'address': r.get('address'), 'location': loc}
        if r.get('shape'):
            d['shape'] = r['shape']
        docs.append({'_index': ZONES, '_source': d})
    ok, errs = helpers.bulk(es, docs, raise_on_error=False)
    print(f'zones indexed {ok}, errors {len(errs)}')
    if errs:  # retry without polygons if any shape is invalid
        helpers.bulk(es, [{'_index': ZONES, '_source': {k: v for k, v in e['index']['data'].items() if k != 'shape'}}
                          for e in errs if 'data' in e.get('index', {})], raise_on_error=False)


# ---------- tools the Mistral agent can call ----------
def lookup_place(text):
    """Geocode with Elasticsearch: a strong match on a park name or a 311 address, else not found."""
    import re
    q = re.sub(r'(?i)\b(san francisco|sf|ca|california|intersection of|street|st\.?)\b|,', ' ', text).strip()
    r = es.search(index=[ZONES, HAZARDS], size=1, query={'bool': {'should': [
        {'match': {'name': {'query': q, 'operator': 'and', 'boost': 2}}},
        {'match': {'address': {'query': q, 'operator': 'and'}}}]}},
        source=['name', 'address', 'location', 'neighborhood'])
    if not r['hits']['hits']:
        return {'found': False, 'hint': 'use your own best estimate of SF coordinates'}
    return {'found': True, **r['hits']['hits'][0]['_source']}


_semantic = None


def hazard_query():
    """Mistral semantic search if the hazard index has our semantic_text field, else category match."""
    global _semantic
    if _semantic is None:
        props = next(iter(es.indices.get_mapping(index=HAZARDS).values()))['mappings']['properties']
        _semantic = props.get('text', {}).get('type') == 'semantic_text'
        print(f'[hazard search: {"Mistral semantic_text" if _semantic else "category fallback"} on {HAZARDS}]')
    if _semantic:
        return {'semantic': {'field': 'text', 'query': HAZARD_QUERY}}
    return {'match_all': {}}  # hazard types are filtered in find_landing_zones


def find_landing_zones(lat, lon, max_walk_m=800, min_acres=0.2):
    """Candidate zones near the drop-off, each scored by Mistral-semantic hazard reports around it."""
    here = {'lat': lat, 'lon': lon}
    r = es.search(index=ZONES, size=10, query={'bool': {'filter': [
        {'geo_distance': {'distance': f'{max_walk_m}m', 'location': here}},
        {'range': {'acres': {'gte': min_acres}}}]}},
        sort=[{'_geo_distance': {'location': here, 'order': 'asc', 'unit': 'm'}}],
        source=['name', 'type', 'property_type', 'acres', 'neighborhood', 'location'])
    zones = []
    for h in r['hits']['hits']:
        z = h['_source'] | {'walk_m': round(h['sort'][0])}
        z['acres'] = z.get('acres') or 0
        # hazards: open 311 reports of hazard types within 150 m (Elastic geo filter + terms agg),
        # ranked by Mistral semantic similarity to "unsafe to land here" so the agent sees the worst ones
        hz = es.search(index=HAZARDS, size=3, query={'bool': {
            'should': [hazard_query()],
            'filter': [{'geo_distance': {'distance': '150m', 'location': z['location']}},
                       {'term': {'status': 'Open'}}, {'terms': {'service_name': HAZARD_TYPES}}]}},
            aggs={'kinds': {'terms': {'field': 'service_name', 'size': 5}}},
            source=['service_name', 'text', 'requested_at'])
        z['open_hazards_150m'] = hz['hits']['total']['value']
        z['hazard_kinds'] = {b['key']: b['doc_count'] for b in hz['aggregations']['kinds']['buckets']}
        z['worst_reports'] = [h['_source'].get('text', h['_source']['service_name'])[:120] for h in hz['hits']['hits']]
        z['score'] = round(10 * math.log1p(z['acres']) - 1.5 * z['open_hazards_150m'] - z['walk_m'] / 100, 1)
        zones.append(z)
    return sorted(zones, key=lambda z: -z['score'])


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'lookup_place', 'description': 'Geocode an SF street address, intersection or park name.',
        'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}},
    {'type': 'function', 'function': {
        'name': 'find_landing_zones',
        'description': 'Rank open-space drone landing zones near a drop-off point by size, walking distance '
                       'and open 311 hazard reports within 150 m.',
        'parameters': {'type': 'object', 'properties': {
            'lat': {'type': 'number'}, 'lon': {'type': 'number'},
            'max_walk_m': {'type': 'integer', 'description': 'max walk from zone to customer, default 800'},
            'min_acres': {'type': 'number', 'description': 'minimum open area, default 0.2'}},
            'required': ['lat', 'lon']}}},
]
IMPL = {'lookup_place': lookup_place, 'find_landing_zones': find_landing_zones}


def ask(request):
    global es
    es = base.es_client()
    msgs = [{'role': 'system', 'content':
             'You are DropZone SF, the dispatcher for a food-delivery drone in San Francisco. '
             'Locate the customer (use lookup_place; if it fails, use your own best SF coordinates), '
             'then call find_landing_zones. Pick ONE zone, give a runner-up, and in <=5 lines explain why: '
             'size, walk distance, and the hazard reports. Never pick a zone with many open hazards if a '
             'cleaner one exists. Reply in the language of the request.'},
            {'role': 'user', 'content': request}]
    for _ in range(6):
        r = base.mistral.chat.complete(model=CHAT, messages=msgs, tools=TOOLS, tool_choice='auto')
        m = r.choices[0].message
        if not m.tool_calls:
            print('\n[Mistral dispatcher]\n' + m.content)
            return
        msgs.append({'role': 'assistant', 'content': m.content or '', 'tool_calls': [
            {'id': c.id, 'type': 'function', 'function': {'name': c.function.name, 'arguments': c.function.arguments}}
            for c in m.tool_calls]})
        for c in m.tool_calls:
            args = c.function.arguments
            args = json.loads(args) if isinstance(args, str) else args
            out = IMPL[c.function.name](**args)
            print(f'\n[Mistral -> Elastic] {c.function.name}({json.dumps(args)})')
            if c.function.name == 'find_landing_zones':
                for z in out[:5]:
                    print(f"  {z['score']:6.1f}  {z['name']:38s} {z['acres']:6.1f} ac  {z['walk_m']:4d} m  "
                          f"hazards {z['open_hazards_150m']} {z['hazard_kinds']}")
            else:
                print('  ', out)
            msgs.append({'role': 'tool', 'name': c.function.name, 'tool_call_id': c.id,
                         'content': json.dumps(out, default=str)[:6000]})


if __name__ == '__main__':
    if sys.argv[1:2] == ['setup']:
        setup()
    elif sys.argv[1:2] == ['ask']:
        ask(' '.join(sys.argv[2:]))
    else:
        print(__doc__)
