import os
import json
import osmnx as ox
import geopandas as gpd
import pandas as pd
import folium
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# ==========================================
# CONFIGURAÇÕES GERAIS
# ==========================================
CRS_WGS84 = 4326
CRS_LOCAL_UTM = 31983  # SIRGAS 2000 / UTM zone 23S

# ==========================================
# PROCESSAMENTO DE DADOS
# ==========================================
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
    
    # 1. Busca os dados no OSM
    features = ox.features.features_from_bbox(city_geom.total_bounds, query_dict)
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
        gdf = gpd.GeoDataFrame(pd.concat([gdf, midpoints_gdf], ignore_index=True), geometry='geometry')

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

def _add_interactive_legend(folium_map, tag_list, color_map, fg_js_names):
    """Injeta HTML e JavaScript para criar a legenda com checkboxes funcionais."""
    legend_html = '''
    <div id="custom-legend" style="position: fixed; bottom: 50px; right: 50px; width: 280px; 
         background-color: white; border:2px solid grey; z-index:9999; font-size:14px;
         padding: 10px; border-radius: 5px; max-height: 70vh; overflow-y: auto;">
         <p style="margin: 0 0 15px 0; font-weight: bold; font-size: 16px;">Legenda</p>
         <div id="legend-items"></div>
    </div>
    '''
    folium_map.get_root().html.add_child(folium.Element(legend_html))
    
    script = f'''
    <script>
    var checkExist = setInterval(function() {{
        var myMap = window["{folium_map.get_name()}"];
        if (!myMap) return;
        clearInterval(checkExist);
        
        var tagNames = {json.dumps(tag_list)};
        var colorMap = {json.dumps(color_map)};
        var fgVars = {json.dumps(fg_js_names)}; 
        
        var legendItems = document.getElementById('legend-items');
        
        tagNames.forEach(function(tag, index) {{
            var color = colorMap[tag];
            var item = document.createElement('div');
            item.style.margin = '8px 0';
            item.style.display = 'flex';
            item.style.alignItems = 'center';
            item.innerHTML = '<input type="checkbox" id="checkbox_' + index + '" checked style="margin-right: 8px; cursor: pointer;">' +
                             '<i style="background:' + color + '; width: 16px; height: 16px; display: inline-block; margin-right: 8px; border-radius: 50%;"></i>' +
                             '<label for="checkbox_' + index + '" style="margin: 0; cursor: pointer; flex-grow: 1;">' + tag + '</label>';
            legendItems.appendChild(item);
            
            document.getElementById('checkbox_' + index).addEventListener('change', function(e) {{
                var layer = window[fgVars[tag]];
                if (layer) e.target.checked ? myMap.addLayer(layer) : myMap.removeLayer(layer);
            }});
        }});
    }}, 100);
    </script>
    '''
    folium_map.get_root().html.add_child(folium.Element(script))

# ==========================================
# CRIAÇÃO DO MAPA
# ==========================================
def create_map(features_points, city_geom, key, columns_to_show=None):
    """Cria o mapa Folium com ícones, linhas e a legenda interativa."""
    columns_to_show = columns_to_show or ['name']
    center = [city_geom.geometry.iloc[0].centroid.y, city_geom.geometry.iloc[0].centroid.x]
    
    m = folium.Map(location=center, zoom_start=11, tiles='Cartodb Positron')
    
    # Se key é None, usamos a coluna '_key' que rastreia qual tag original foi processada
    grouping_column = '_key' if key is None else key
    
    # Prepara o colormap
    unique_tags = sorted(features_points[grouping_column].unique())
    cmap = plt.colormaps.get_cmap('tab20')
    color_map = {tag: mcolors.to_hex(cmap(i / max(len(unique_tags) - 1, 1))) for i, tag in enumerate(unique_tags)}
    
    # Inicializa as FeatureGroups
    feature_groups = {tag: folium.FeatureGroup(name=str(tag), show=True) for tag in unique_tags}
    
    # Adiciona as geometrias no mapa
    for _, row in features_points.iterrows():
        if row.geometry.is_empty: continue
            
        geom = row.geometry
        color = color_map.get(row[grouping_column], 'gray')
        fg = feature_groups[row[grouping_column]]
        popup = _build_popup(row, columns_to_show)
        
        if geom.geom_type in ['LineString', 'MultiLineString']:
            geoms_to_plot = [geom] if geom.geom_type == 'LineString' else geom.geoms
            for line in geoms_to_plot:
                coords = [(c[1], c[0]) for c in line.coords]
                folium.PolyLine(coords, color=color, weight=2, opacity=0.8, popup=popup).add_to(fg)
        else:
            # Pontos (originais ou gerados por centroide)
            folium.CircleMarker(
                location=[geom.y, geom.x], radius=4, color=color, 
                fill=True, fillColor=color, fillOpacity=0.7, popup=popup
            ).add_to(fg)
    
    # Extrai as variáveis internas do JS geradas pelo Folium
    fg_js_names = {tag: fg.add_to(m).get_name() for tag, fg in feature_groups.items()}
    
    # Adiciona a legenda controlável
    _add_interactive_legend(m, unique_tags, color_map, fg_js_names)
    
    return m

# ==========================================
# FUNÇÕES DE EXPORTAÇÃO E ORQUESTRAÇÃO
# ==========================================
def save_files(m, features_points, save_path, key, tags_name=None):
    """Salva os resultados em HTML, Parquet e PMTiles."""
    os.makedirs(save_path, exist_ok=True)
    
    # Define o suffix do arquivo baseado na chave
    if key is None:
        file_suffix = tags_name if tags_name else "all"
    else:
        file_suffix = key
    
    # HTML
    html_file = f"docs/mapas/features_map_{file_suffix}.html"
    os.makedirs(os.path.dirname(html_file), exist_ok=True)
    m.save(html_file)
    print(f"Map saved: {html_file}")
    
    # Parquet
    pq_file = os.path.join(save_path, f"features_{file_suffix}.parquet")
    features_points.to_parquet(pq_file, compression="snappy")
    print(f"Parquet saved: {pq_file}")
    
    # PMTiles
    pmt_file = os.path.join(save_path, f"features_{file_suffix}.pmtiles")
    if os.path.exists(pmt_file): os.remove(pmt_file)
    try:
        features_points.to_file(pmt_file, driver="PMTiles", engine="pyogrio", encoding="utf-8", MINZOOM=0, MAXZOOM=14, NAME=f"layer_{file_suffix}")
        print(f"PMTiles saved: {pmt_file}")
    except UnicodeEncodeError:
        print(f"Warning: Could not save PMTiles due to encoding issues. Skipping PMTiles export.")

def process_key(key=None, tags=None, city_geom=None, save_path="Dados/Saída/", columns_to_show=None, tags_name=None):
    """
    Função principal que orquestra a execução ponta a ponta.
    Se key é None, processa todas as chaves em tags simultaneamente.
    tags_name: nome da variável tags para usar no output (ex: 'amenities', 'buildings')
    """
    if key is None:
        # Processa todas as chaves simultaneamente
        output_name = tags_name if tags_name else "all"
        print(f"\n{'='*50}\nProcessing all tags ({output_name})...\n{'='*50}")
        
        features_points = fetch_and_process_features(city_geom, None, tags)
        m = create_map(features_points, city_geom, None, columns_to_show)
        save_files(m, features_points, save_path, None, tags_name)
        
        print(f"✓ Completed processing all tags\n")
        return features_points
    else:
        # Processa uma chave específica
        print(f"\n{'='*50}\nProcessing {key}...\n{'='*50}")
        
        features_points = fetch_and_process_features(city_geom, key, tags)
        m = create_map(features_points, city_geom, key, columns_to_show)
        save_files(m, features_points, save_path, key, None)
        
        print(f"✓ Completed {key}\n")
        return features_points