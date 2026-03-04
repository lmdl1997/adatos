import json
import os
import csv
import unicodedata
import re

def normalize(s):
    if not s: return ""
    s = str(s).lower()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def load_csv_data(filepath):
    # Mapping based on names for robustness
    # (state_norm, muni_norm, parish_norm) -> parish_name
    parishes = {}
    # (state_norm, muni_norm) -> muni_name
    munis = {}
    # state_norm -> state_name
    states = {}
    
    # Store candidates per municipality
    muni_tree = {} 

    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            s, m, p = row['state'], row['municipality'], row['parish']
            sn, mn, pn = normalize(s), normalize(m), normalize(p)
            
            states[sn] = s
            munis[(sn, mn)] = m
            parishes[(sn, mn, pn)] = p
            
            if sn not in muni_tree: muni_tree[sn] = {}
            if mn not in muni_tree[sn]: muni_tree[sn][mn] = []
            muni_tree[sn][mn].append(p)
            
    return states, munis, parishes, muni_tree

def find_best_parish(sn, mn, pn, p_csv, muni_tree):
    # Specific fix for Timotes
    if sn == normalize("Mérida") and mn == normalize("Miranda") and (pn == normalize("Miranda") or pn == normalize("Capital Miranda")):
        return "Timotes"
    
    # Precise match
    if (sn, mn, pn) in p_csv:
        return p_csv[(sn, mn, pn)]
    
    # Heuristic matching within municipality
    if sn in muni_tree and mn in muni_tree[sn]:
        cands = muni_tree[sn][mn]
        # Match if HDX parish == CSV municipality name (capital parish case)
        if pn == mn:
            for c in cands:
                if normalize(c) == mn: return c
            return cands[0] if cands else None
            
        # Partial match
        for c in cands:
            cn = normalize(c)
            if cn in pn or pn in cn: return c
            
        # Fallback to first if only one
        if len(cands) == 1: return cands[0]
        
    return None

def process():
    csv_file = 'data-1772651932399.csv'
    states_csv, munis_csv, parishes_csv, muni_tree = load_csv_data(csv_file)
    
    def save_json(data, path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 1. Level 1 (States)
    adm1 = load_json('ven_admin1.geojson')
    state_map = {} # name_norm -> final_name
    for f in adm1['features']:
        hdx_s = f['properties']['adm1_name']
        sn = normalize(hdx_s)
        final_s = states_csv.get(sn, hdx_s)
        state_map[sn] = final_s
        f['properties'] = {"Pais": "Venezuela", "Estado": final_s}
    save_json(adm1, 'venezuela_estados.geojson')

    # 2. Level 2 (Municipios)
    adm2 = load_json('ven_admin2.geojson')
    muni_pcode_map = {} # pcode -> (state, muni)
    for f in adm2['features']:
        hdx_s = f['properties']['adm1_name']
        hdx_m = f['properties']['adm2_name']
        sn, mn = normalize(hdx_s), normalize(hdx_m)
        
        final_s = state_map.get(sn, hdx_s)
        final_m = munis_csv.get((sn, mn), hdx_m)
        
        # Explicit override for Juan Jose Rondon
        if sn == normalize("Guárico") and (mn == normalize("Las Mercedes") or mn == normalize("Juan José Rondón")):
            final_m = "Juan José Rondón"
            
        muni_pcode_map[f['properties']['adm2_pcode']] = (final_s, final_m)
        f['properties'] = {"Pais": "Venezuela", "Estado": final_s, "Municipio": final_m}
    save_json(adm2, 'venezuela_municipios.geojson')

    # 3. Level 3 (Parroquias)
    adm3 = load_json('ven_admin3.geojson')
    for f in adm3['features']:
        hdx_s = f['properties']['adm1_name']
        hdx_m = f['properties']['adm2_name']
        hdx_p = f['properties']['adm3_name']
        m_pcode = f['properties']['adm2_pcode']
        
        sn, mn, pn = normalize(hdx_s), normalize(hdx_m), normalize(hdx_p)
        
        # Use hierarchy from Level 2
        final_s, final_m = muni_pcode_map.get(m_pcode, (hdx_s, hdx_m))
        
        # Match parish
        final_p = find_best_parish(normalize(final_s), normalize(final_m), pn, parishes_csv, muni_tree)
        if not final_p:
            final_p = hdx_p.replace("(Capital)", "").replace("Capital ", "").strip()

        f['properties'] = {
            "Pais": "Venezuela",
            "Estado": final_s,
            "Municipio": final_m,
            "Parroquia": final_p
        }
    save_json(adm3, 'venezuela_parroquias.geojson')

    # 4. SPLITTING
    import shutil
    for d in ['estados', 'municipios']:
        if os.path.exists(d): shutil.rmtree(d)
        os.makedirs(d)

    for f in adm1['features']:
        s = f['properties']['Estado']
        save_json({"type": "FeatureCollection", "features": [f]}, f"estados/{s.lower().replace(' ', '_')}.geojson")
    
    by_s_m = {}
    for f in adm2['features']:
        s = f['properties']['Estado']; by_s_m.setdefault(s, []).append(f)
    for s, feats in by_s_m.items():
        save_json({"type": "FeatureCollection", "features": feats}, f"estados/{s.lower().replace(' ', '_')}_municipios.geojson")
        
    by_s_p = {}
    for f in adm3['features']:
        s = f['properties']['Estado']; by_s_p.setdefault(s, []).append(f)
    for s, feats in by_s_p.items():
        save_json({"type": "FeatureCollection", "features": feats}, f"estados/{s.lower().replace(' ', '_')}_parroquias.geojson")

    # Reload original for pcodes
    adm2_orig = load_json('ven_admin2.geojson')
    for f_orig, f_proc in zip(adm2_orig['features'], adm2['features']):
        m = f_proc['properties']['Municipio']
        pc = f_orig['properties']['adm2_pcode']
        save_json({"type": "FeatureCollection", "features": [f_proc]}, f"municipios/{m.lower().replace(' ', '_')}_{pc.lower()}.geojson")

    adm3_orig = load_json('ven_admin3.geojson')
    m_p = {}
    for f_orig, f_proc in zip(adm3_orig['features'], adm3['features']):
        pc = f_orig['properties']['adm2_pcode']
        m = f_proc['properties']['Municipio']
        m_p.setdefault((m, pc), []).append(f_proc)
    for (m, pc), feats in m_p.items():
        save_json({"type": "FeatureCollection", "features": feats}, f"municipios/{m.lower().replace(' ', '_')}_{pc.lower()}_parroquias.geojson")

    # Countries
    adm0 = load_json('ven_admin0.geojson')
    for f in adm0['features']: f['properties'] = {"Pais": "Venezuela"}
    save_json(adm0, 'venezuela_pais.geojson')

    print("FORCE_SYNC_SUCCESS")

if __name__ == "__main__":
    process()
