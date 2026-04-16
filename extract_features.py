import os
import json
import time
import osmnx as ox
import geopandas as gpd
import pandas as pd
import folium
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import requests

# ==========================================
# CONFIGURAÇÕES GERAIS
# ==========================================
CRS_WGS84 = 4326
CRS_LOCAL_UTM = 31983  # SIRGAS 2000 / UTM zone 23S

# ==========================================
# CONFIGURAÇÃO DE CORES - BIKE INFRASTRUCTURE
# ==========================================
# Cores hex para cada categoria de infraestrutura de bicicleta
BIKE_INFRASTRUCTURE_COLORS = {
    'Calçadas': '#00AA00',        # Green
    'Ciclovias': '#001F7F',       # Dark blue
    'Ciclofaixas': '#0074E4',     # Blue
    'Ciclorrotas': '#87CEEB'      # Light blue
}

# Categorias que devem ter linhas tracejadas
BIKE_INFRASTRUCTURE_DASH_LINES = {'Ciclorrotas'}

# ==========================================
# CONFIGURAÇÃO DE CORES - FOOTWAY INFRASTRUCTURE
# ==========================================
# Cores hex para cada categoria de infraestrutura de pedestres
FOOTWAY_INFRASTRUCTURE_COLORS = {
    'Faixas de Pedestres': '#FF4444',     # Red
    'Calçadas Formais': '#00AA00',        # Green
    'Calçadas Informais': '#FFAA00',      # Orange
    'Áreas Pedestrianizadas': '#9933FF',  # Purple
    'Corredores': '#0074E4'               # Blue
}

# Categorias que devem ter linhas tracejadas
FOOTWAY_INFRASTRUCTURE_DASH_LINES = {'Áreas Pedestrianizadas'}

# ==========================================
# PROCESSAMENTO DE DADOS
# ==========================================
RETRYABLE_FETCH_ERRORS = (
    requests.exceptions.RequestException,
)


def _fetch_features_with_retry(city_geom, query_dict, retries=3, wait_seconds=60):
    """Busca dados no OSM com retentativa para falhas transitórias de rede."""
    for attempt in range(1, retries + 1):
        try:
            return ox.features.features_from_bbox(city_geom.total_bounds, query_dict)
        except RETRYABLE_FETCH_ERRORS as err:
            if attempt == retries:
                print(
                    f"[retry] OSM fetch failed after {retries} attempts. "
                    f"Last error: {type(err).__name__}: {err}"
                )
                raise

            print(
                f"[retry] OSM fetch attempt {attempt}/{retries} failed with "
                f"{type(err).__name__}: {err}. Retrying in {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)


def fetch_and_process_features(city_geom, key, tags):
    """Busca features no OSM, converte polígonos em centroides e adiciona pontos médios às linhas.
    Se key é None, processa todas as chaves em tags simultaneamente."""
    
    # Se key é None, processa todas as chaves
    if key is None:
        print(f"Buscando por features para todas as tags...\n")
        query_dict = tags
    else:
        print(f"Buscando por features com a tag '{key}': \n{tags[key]}\n")
        query_dict = {key: tags[key]}
    
    # 1. Busca os dados no OSM (com retry para erros de conexão/transporte)
    features = _fetch_features_with_retry(city_geom, query_dict, retries=3, wait_seconds=60)
    print(f"Quantidade original de features: {len(features)}\n")
    
    # 2. Converte para CRS local (metros) para cálculos precisos
    gdf = features.copy().to_crs(epsg=CRS_LOCAL_UTM)
    
    # 3. Se processamos todas as chaves, rastreamos qual chave cada feature pertence
    if key is None:
        # Determina a chave para cada feature baseado nas colunas presentes
        def get_feature_key(row):
            for tag_key in tags.keys():
                if tag_key in row.index and pd.notna(row[tag_key]):
                    return tag_key
            return None
        gdf['_key'] = gdf.apply(get_feature_key, axis=1)
    
    # 4. Máscaras para separar tipos de geometria
    is_polygon = gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])
    is_line = gdf.geometry.geom_type.isin(['LineString', 'MultiLineString'])
    
    print("Geometrias encontradas:")
    print(f"  - Polygons/MultiPolygons: {is_polygon.sum()}")
    print(f"  - Lines/MultiLines: {is_line.sum()}\n")
    
    # 5. Transforma Polígonos em Centroides
    if is_polygon.any():
        gdf.loc[is_polygon, 'geometry'] = gdf.loc[is_polygon, 'geometry'].centroid
    
    # 6. Para linhas, mantém as originais E adiciona um ponto no meio
    if is_line.any():
        midpoints_gdf = gdf.loc[is_line].copy()
        midpoints_gdf.geometry = midpoints_gdf.geometry.interpolate(0.5, normalized=True)
        #gdf = gpd.GeoDataFrame(pd.concat([gdf, midpoints_gdf], ignore_index=True), geometry='geometry')

    # 7. Retorna para Lat/Lon padrão e filtra os que estão estritamente dentro da cidade
    gdf = gdf.to_crs(epsg=CRS_WGS84)
    gdf_filtered = gdf[gdf.within(city_geom.geometry.iloc[0])].copy()
    
    print(f"-> {len(gdf_filtered)} features mantidas dentro dos limites da cidade após processamento.\n")
    return gdf_filtered

# ==========================================
# FUNÇÕES AUXILIARES DO MAPA (HTML / JS)
# ==========================================
def _build_popup(row, columns_to_show):
    """Monta o card HTML do popup para cada geometria."""
    html = "<div style='font-family: Arial; font-size: 12px; width: 250px;'>"
    html += "<b>Detalhes da Feature OSM</b><br><hr>"
    html += f"<b>Tipo:</b> {row.geometry.geom_type}<br>"
    
    for col in columns_to_show:
        if col != 'geometry' and col in row.index:
            val = str(row[col])
            val = val[:100] + "..." if len(val) > 100 else val
            html += f"<b>{col}:</b> {val}<br>"
            
    html += "</div>"
    return folium.Popup(html, max_width=300)

def _categorize_bike_features(row):
    """Categoriza features de bike/pedestrian baseado nas especificações OSM.
    
    Categorias:
    - Ciclovias: Vias protegidas e exclusivas (highway=cycleway ou cycleway=track)
    - Calçadas compartilhadas: Calçadas com circulação compartilhada (dashed lines)
    - Ciclofaixas: Vias exclusivas sem segregação física (cycleway=lane)
    - Ciclorrotas: Ruas compartilhadas com preferência pra bicicletas (dashed lines)
    """
    
    # 1. CICLOVIAS - Ciclovias protegidas e exclusivas
    # highway = cycleway
    if pd.notna(row.get('highway')) and row['highway'] == 'cycleway':
        return 'Ciclovias'
    
    # cycleway = track (e variantes)
    track_cols = ['cycleway', 'cycleway:left', 'cycleway:right']
    for col in track_cols:
        if pd.notna(row.get(col)) and row[col] in ['track', 'opposite_track']:
            return 'Ciclovias'
    
    # 2. CALÇADAS COMPARTILHADAS - Calçadas com sinalização para circulação compartilhada
    # (highway=footway & bicycle=designated) OR (highway=pedestrian & bicycle=designated) OR (highway=pedestrian & bicycle=yes)
    sidewalk_conditions = [
        (pd.notna(row.get('highway')) and row['highway'] == 'footway' and 
         pd.notna(row.get('bicycle')) and row['bicycle'] == 'designated'),
        (pd.notna(row.get('highway')) and row['highway'] == 'pedestrian' and 
         pd.notna(row.get('bicycle')) and row['bicycle'] == 'designated'),
        (pd.notna(row.get('highway')) and row['highway'] == 'pedestrian' and 
         pd.notna(row.get('bicycle')) and row['bicycle'] == 'yes')
    ]
    
    if any(sidewalk_conditions):
        return 'Calçadas compartilhadas'
    
    # cycleway = sidepath (e variantes)
    sidepath_cols = ['cycleway', 'cycleway:left', 'cycleway:right']
    for col in sidepath_cols:
        if pd.notna(row.get(col)) and row[col] == 'sidepath':
            return 'Calçadas compartilhadas'
    
    # 3. CICLOFAIXAS - Vias exclusivas sem segregação física
    # cycleway = lane (e variantes)
    lane_cols = ['cycleway', 'cycleway:left', 'cycleway:right', 'cycleway:both']
    for col in lane_cols:
        if pd.notna(row.get(col)) and row[col] in ['lane', 'opposite_lane']:
            return 'Ciclofaixas'
    
    # 4. CICLORROTAS - Ruas compartilhadas com preferência para bicicletas
    # cycleway = buffered_lane, shared_lane, share_busway (e variantes)
    shared_cols = ['cycleway', 'cycleway:left', 'cycleway:right']
    for col in shared_cols:
        if pd.notna(row.get(col)) and row[col] in ['buffered_lane', 'shared_lane', 'share_busway', 'opposite_share_busway']:
            return 'Ciclorrotas'
    
    return None

def _categorize_footway_features(row):
    """Categoriza features de pedestres/footway baseado nas especificações OSM.
    
    Categorias:
    - Faixas de Pedestres: Cruzamentos de pedestres (highway=footway & footway=crossing)
    - Calçadas Formais: Calçadas estruturadas (sidewalk tags, surface, width, etc)
    - Calçadas Informais: Calçadas sem infraestrutura formal (informal=yes)
    - Áreas Pedestrianizadas: Zonas exclusivas de pedestres (highway=pedestrian)
    - Corredores: Corredores internos/entre edifícios (highway=corridor)
    """
    
    # 1. FAIXAS DE PEDESTRES - Cruzamentos de pedestres
    if pd.notna(row.get('highway')) and row['highway'] == 'footway':
        if pd.notna(row.get('footway')) and row['footway'] == 'crossing':
            return 'Faixas de Pedestres'
    
    # 2. CORREDORES - Corredores/passagens entre edifícios
    if pd.notna(row.get('highway')) and row['highway'] == 'corridor':
        return 'Corredores'
    
    # 3. ÁREAS PEDESTRIANIZADAS - Zonas exclusivas de pedestres
    if pd.notna(row.get('highway')) and row['highway'] == 'pedestrian':
        return 'Vias de Pedestres'
    
    # 4. CALÇADAS INFORMAIS - Calçadas sem infraestrutura formal
    if pd.notna(row.get('informal')) and row['informal'] == 'yes':
        return 'Calçadas Informais'

    # 5. CALÇADAS FORMAIS - Calçadas estruturadas
    # Critérios: sidewalk tags, surface (asphalt/concrete), width, maxheight (indica via formal)
    # ou foot=designated, ou área com surface
    formal_indicators = [
        (pd.notna(row.get('highway')) and row['highway'] == 'footway'),
        (pd.notna(row.get('sidewalk')) and row['sidewalk'] in ['yes', 'both', 'left', 'right']),
        (pd.notna(row.get('foot')) and row['foot'] in ['designated', 'yes'])
        #pd.notna(row.get('surface')) and row['surface'] in ['asphalt', 'concrete', 'paving_stones', 'cobblestone'],
        #pd.notna(row.get('width')),
        #pd.notna(row.get('lit')) and row['lit'] in ['yes', 'automatic'],
        #pd.notna(row.get('maxheight')),
        #pd.notna(row.get('access')) and row['access'] != 'private'
        ]
        
    if any(formal_indicators):  # Pelo menos 1 indicador de infraestrutura formal
        return 'Calçadas Formais'
    
    return None

def _add_native_legend(folium_map, color_map):
    """Adiciona legenda de cores visível ao mapa usando folium.Element."""
    
    legend_html = '''
    <div id="legend-container" style="
        position: fixed;
        bottom: 50px;
        left: 50px;
        width: 220px;
        background-color: white;
        border: 2px solid #ccc;
        border-radius: 5px;
        padding: 12px;
        z-index: 9999;
        font-size: 13px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.2);
        font-family: Arial, sans-serif;
    ">
        <div style="font-weight: bold; font-size: 14px; margin-bottom: 10px; color: #333;">Legenda</div>
        <div id="legend-items"></div>
    </div>
    '''
    
    script = f"""
    <script>
        var colorMap = {json.dumps(color_map)};
        var container = document.getElementById('legend-items');
        
        for (var tag in colorMap) {{
            var color = colorMap[tag];
            
            var item = document.createElement('div');
            item.style.display = 'flex';
            item.style.alignItems = 'center';
            item.style.marginBottom = '8px';
            item.style.gap = '8px';
            
            var box = document.createElement('div');
            box.style.width = '16px';
            box.style.height = '16px';
            box.style.borderRadius = '50%';
            box.style.border = '1px solid #999';
            box.style.backgroundColor = color;
            box.style.flexShrink = '0';
            
            var label = document.createElement('div');
            label.style.color = '#333';
            label.style.fontSize = '13px';
            label.textContent = tag;
            label.title = tag;
            
            item.appendChild(box);
            item.appendChild(label);
            container.appendChild(item);
        }}
    </script>
    """
    
    folium_map.get_root().html.add_child(folium.Element(legend_html + script))

# ==========================================
# CRIAÇÃO DO MAPA
# ==========================================
def create_map(features_points, city_geom, key, columns_to_show=None, use_custom_type=False, custom_type='bike'):
    """Cria o mapa Folium com ícones, linhas e a legenda interativa.
    
    Args:
        use_custom_type: Se True, usa a coluna '_type' para categorização personalizada
        custom_type: Tipo de categorização ('bike' ou 'footway')
                     Aplica automaticamente cores predefinidas para o tipo especificado.
    """
    columns_to_show = columns_to_show or ['name']
    center = [city_geom.geometry.iloc[0].centroid.y, city_geom.geometry.iloc[0].centroid.x]
    
    m = folium.Map(location=center, zoom_start=11, tiles='Cartodb Positron')
    
    # Determina qual coluna usar para agrupamento
    if use_custom_type and '_type' in features_points.columns:
        grouping_column = '_type'
    elif key is None:
        grouping_column = '_key'
    else:
        grouping_column = key
    
    # Remove NaN values para evitar linhas vazias na legenda
    features_clean = features_points.dropna(subset=[grouping_column])
    
    # Prepara o colormap
    unique_tags = sorted(features_clean[grouping_column].unique())
    
    # Usa cores predefinidas para tipo customizado ou gera automaticamente
    if use_custom_type and grouping_column == '_type':
        if custom_type == 'footway':
            color_map = {tag: FOOTWAY_INFRASTRUCTURE_COLORS.get(tag, '#808080') for tag in unique_tags}
            dash_lines = FOOTWAY_INFRASTRUCTURE_DASH_LINES
        else:  # default para bike
            color_map = {tag: BIKE_INFRASTRUCTURE_COLORS.get(tag, '#808080') for tag in unique_tags}
            dash_lines = BIKE_INFRASTRUCTURE_DASH_LINES
    else:
        cmap = plt.colormaps.get_cmap('tab20')
        color_map = {tag: mcolors.to_hex(cmap(i / max(len(unique_tags) - 1, 1))) for i, tag in enumerate(unique_tags)}
        dash_lines = set()
    
    # Inicializa as FeatureGroups
    feature_groups = {tag: folium.FeatureGroup(name=str(tag), show=True) for tag in unique_tags}
    
    # Adiciona as geometrias no mapa
    for _, row in features_clean.iterrows():
        if row.geometry.is_empty: continue
            
        geom = row.geometry
        tag = row[grouping_column]
        color = color_map.get(tag, 'gray')
        fg = feature_groups[tag]
        popup = _build_popup(row, columns_to_show)
        
        if geom.geom_type in ['LineString', 'MultiLineString']:
            geoms_to_plot = [geom] if geom.geom_type == 'LineString' else geom.geoms
            for line in geoms_to_plot:
                coords = [(c[1], c[0]) for c in line.coords]
                # Usa dasharray para linhas tracejadas
                dash_array = '5, 5' if tag in dash_lines else None
                folium.PolyLine(
                    coords, color=color, weight=2, opacity=0.8, 
                    popup=popup, dash_array=dash_array
                ).add_to(fg)
        else:
            # Pontos (originais ou gerados por centroide)
            folium.CircleMarker(
                location=[geom.y, geom.x], radius=4, color=color, 
                fill=True, fillColor=color, fillOpacity=0.7, popup=popup
            ).add_to(fg)
    
    # Adiciona FeatureGroups ao mapa
    for tag, fg in feature_groups.items():
        fg.add_to(m)
    
    # Adiciona LayerControl nativo do Folium (funciona no GitHub Pages)
    # Isso cria checkboxes para cada FeatureGroup automaticamente
    folium.LayerControl().add_to(m)
    
    # Adiciona legenda visual de cores
    _add_native_legend(m, color_map)
    
    return m

# ==========================================
# FUNÇÕES DE EXPORTAÇÃO E ORQUESTRAÇÃO
# ==========================================
def save_files(m, features_points, save_path, key, tags_name=None, cd_mun=None, theme_name=None):
    """Salva os resultados em HTML, Parquet e PMTiles com organização por cd_mun e tema."""
    if cd_mun is None or str(cd_mun).strip() == "":
        raise ValueError("cd_mun is required and cannot be empty.")

    if theme_name is None or str(theme_name).strip() == "":
        raise ValueError("theme_name is required and cannot be empty.")

    cd_mun = str(cd_mun)
    theme_name = str(theme_name)
    features_points = features_points.copy()

    if "id" in features_points.columns:
        features_points["id"] = features_points["id"].astype(str)

    # Dados: organização por tema (Dados/Saída/{theme}/features_{cd_mun}.{ext})
    data_theme_dir = os.path.join(save_path, theme_name)
    os.makedirs(data_theme_dir, exist_ok=True)

    # Docs: organização por município/tema (docs/mapas/{cd_mun}/{theme}/features_map.html)
    html_dir = os.path.join("docs", "mapas", cd_mun, theme_name)
    os.makedirs(html_dir, exist_ok=True)
    html_file = os.path.join(html_dir, "features_map.html")
    m.save(html_file)
    print(f"Map saved: {html_file}")

    manifest_file = os.path.join("docs", "mapas", "manifest.json")
    manifest = {}
    if os.path.exists(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except json.JSONDecodeError:
            manifest = {}
    manifest.setdefault(cd_mun, {})[theme_name] = f"mapas/{cd_mun}/{theme_name}/features_map.html"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    # Normaliza IDs para evitar falha do PyArrow com valores mistos (ex: 8893 e "P8893").
    parquet_gdf = features_points.copy()
    if "id" in parquet_gdf.columns:
        parquet_gdf["id"] = parquet_gdf["id"].astype(str)
    if (
        parquet_gdf.index.name == "id"
        or (
            isinstance(parquet_gdf.index, pd.MultiIndex)
            and "id" in [name for name in parquet_gdf.index.names if name is not None]
        )
    ):
        parquet_gdf = parquet_gdf.reset_index()
        if "id" in parquet_gdf.columns:
            parquet_gdf["id"] = parquet_gdf["id"].astype(str)

    pq_file = os.path.join(data_theme_dir, f"features_{cd_mun}.parquet")
    parquet_gdf.to_parquet(pq_file, compression="snappy", index=False)
    print(f"Parquet saved: {pq_file}")

    pmt_file = os.path.join(data_theme_dir, f"features_{cd_mun}.pmtiles")
    if os.path.exists(pmt_file):
        os.remove(pmt_file)
    try:
        features_points.to_file(
            pmt_file,
            driver="PMTiles",
            engine="pyogrio",
            encoding="utf-8",
            MINZOOM=0,
            MAXZOOM=14,
            NAME=f"layer_{theme_name}_{cd_mun}"
        )
        print(f"PMTiles saved: {pmt_file}")
    except UnicodeEncodeError:
        print("Warning: Could not save PMTiles due to encoding issues. Skipping PMTiles export.")

def process_key(key=None, tags=None, city_geom=None, save_path="Dados/Saída/", columns_to_show=None, tags_name=None, use_custom_type=False, custom_type='bike', cd_mun=None, theme_name=None):
    """
    Função principal que orquestra a execução ponta a ponta.
    Se key é None, processa todas as chaves em tags simultaneamente.
    tags_name: nome da variável tags para usar no output (ex: 'amenities', 'buildings')
    use_custom_type: Se True, aplica categorização personalizada
    custom_type: Tipo de categorização ('bike' ou 'footway')
                 - 'bike': Categorização de infraestrutura de bicicletas
                 - 'footway': Categorização de infraestrutura de pedestres
    """
    if cd_mun is None or str(cd_mun).strip() == "":
        raise ValueError("cd_mun is required and cannot be empty.")

    resolved_theme_name = theme_name if theme_name else (tags_name if key is None else key)
    if resolved_theme_name is None or str(resolved_theme_name).strip() == "":
        raise ValueError("theme_name could not be resolved. Provide theme_name explicitly.")

    if key is None:
        # Processa todas as chaves simultaneamente
        output_name = tags_name if tags_name else "all"
        print(f"\n{'='*50}\nProcessing all tags ({output_name}) for cd_mun={cd_mun}...\n{'='*50}")
        
        features_points = fetch_and_process_features(city_geom, None, tags)
        
        # Aplica categorização personalizada se solicitado
        if use_custom_type:
            if custom_type == 'footway':
                features_points['_type'] = features_points.apply(_categorize_footway_features, axis=1)
            else:  # default para bike
                features_points['_type'] = features_points.apply(_categorize_bike_features, axis=1)
        
        m = create_map(features_points, city_geom, None, columns_to_show, use_custom_type=use_custom_type, custom_type=custom_type)
        save_files(m, features_points, save_path, None, tags_name, cd_mun=cd_mun, theme_name=resolved_theme_name)
        
        print(f"✓ Completed processing all tags\n")
        return features_points
    else:
        # Processa uma chave específica
        print(f"\n{'='*50}\nProcessing {key} for cd_mun={cd_mun}...\n{'='*50}")
        
        features_points = fetch_and_process_features(city_geom, key, tags)
        
        # Aplica categorização personalizada se solicitado
        if use_custom_type:
            if custom_type == 'footway':
                features_points['_type'] = features_points.apply(_categorize_footway_features, axis=1)
            else:  # default para bike
                features_points['_type'] = features_points.apply(_categorize_bike_features, axis=1)
        
        m = create_map(features_points, city_geom, key, columns_to_show, use_custom_type=use_custom_type, custom_type=custom_type)
        save_files(m, features_points, save_path, key, None, cd_mun=cd_mun, theme_name=resolved_theme_name)
        
        print(f"✓ Completed {key}\n")
        return features_points