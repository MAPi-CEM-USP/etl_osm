import osmnx as ox
import geopandas as gpd
import pandas as pd
import folium
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import os
import json

# 2. Fetch and process features
def fetch_and_process_features(city_geom, key, tags):
    """Fetch OSM features, convert polygons to centroids, keep lines as-is"""
    print(f"Buscando por features com a tag '{key}': \n{tags[key]}\n")
    
    # Query OSM
    features = ox.features.features_from_bbox(city_geom.total_bounds, {key: tags[key]})
    print(f"Quantidade de pontos antes do processamento: {len(features)}\n")
    
    # Converter para CRS local para operações geométricas
    features_points = features.copy()
    features_points = features_points.to_crs(epsg=31983)  # SIRGAS 2000 / UTM zone 23S
    
    # Separar geometrias por tipo
    polygon_mask = features_points.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])
    line_mask = features_points.geometry.geom_type.isin(['LineString', 'MultiLineString'])
    
    print(f"Geometrias encontradas:")
    print(f"  - Polygons/MultiPolygons: {polygon_mask.sum()}")
    print(f"  - Lines/MultiLines: {line_mask.sum()}")
    print(f"  - Outras: {(~polygon_mask & ~line_mask).sum()}\n")
    
    # Converter apenas polygons em centroides
    if polygon_mask.any():
        features_points.loc[polygon_mask, 'geometry'] = features_points.loc[polygon_mask, 'geometry'].centroid
    
    # Para linhas, criar novos pontos no meio e adicionar ao dataframe
    if line_mask.any():
        lines_df = features_points.loc[line_mask].copy()
        midpoints_df = lines_df.copy()
        
        # Calcular ponto no meio da linha
        midpoints_df.geometry = midpoints_df.geometry.apply(
            lambda geom: geom.interpolate(0.5, normalized=True)
        )
        
        # Concatenar linhas originais + novos pontos de midpoint
        features_points = gpd.GeoDataFrame(
            pd.concat([features_points, midpoints_df], ignore_index=True),
            geometry='geometry'
        )

    # Voltar para WGS 84
    features_points = features_points.to_crs(epsg=4326)
    
    print("Geometrias convertidas (polygons → centroides, linhas mantidas + midpoints adicionados)\n")
    
    # Filter by city boundary
    features_points = features_points[features_points.within(city_geom.geometry.iloc[0])].copy()
    print(f"{len(features_points)} features inside the city boundary after filtering\n")
    
    return features_points

# 3. Create map
def create_map(features_points, city_geom, key, columns_to_show=None):
    """Create folium map with colored icons and working checkboxes for toggling visibility"""
    if columns_to_show is None:
        columns_to_show = ['name']
    
    center_lat = city_geom.geometry.iloc[0].centroid.y
    center_lon = city_geom.geometry.iloc[0].centroid.x
    
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=11,
        tiles='Cartodb Positron'
    )
    
    # Color map
    unique_tags = features_points[key].unique()
    cmap = plt.colormaps.get_cmap('tab20')
    num_tags = len(unique_tags)
    
    color_map = {
        tag: mcolors.to_hex(cmap(i / max(num_tags - 1, 1)))
        for i, tag in enumerate(unique_tags)
    }
    
    # Criar FeatureGroups para cada tag
    feature_groups = {}
    for tag in sorted(unique_tags):
        feature_groups[tag] = folium.FeatureGroup(name=str(tag), show=True)
    
    # Add markers/lines aos FeatureGroups
    for idx, row in features_points.iterrows():
        if row.geometry.is_empty:
            continue
        
        geom = row.geometry
        tag_value = row[key]
        color = color_map.get(tag_value, 'gray')
        fg = feature_groups[tag_value]
        
        # Build popup HTML
        popup_html = "<div style='font-family: Arial; font-size: 12px; width: 250px;'>"
        popup_html += "<b>OSM Feature Details</b><br><hr>"
        popup_html += f"<b>Geometry Type:</b> {geom.geom_type}<br>"
        
        for col in columns_to_show:
            if col != 'geometry' and col in row.index:
                value = row[col]
                if isinstance(value, str):
                    value = value[:100] if len(value) > 100 else value
                popup_html += f"<b>{col}:</b> {value}<br>"
        
        popup_html += "</div>"
        
        popup = folium.Popup(popup_html, max_width=300)
        
        # Plot linestrings
        if geom.geom_type == 'LineString':
            coords = [(coord[1], coord[0]) for coord in geom.coords]
            folium.PolyLine(
                coords,
                color=color,
                weight=2,
                opacity=0.8,
                popup=popup
            ).add_to(fg)
        
        # Plot multilinestrings
        elif geom.geom_type == 'MultiLineString':
            for line in geom.geoms:
                coords = [(coord[1], coord[0]) for coord in line.coords]
                folium.PolyLine(
                    coords,
                    color=color,
                    weight=2,
                    opacity=0.8,
                    popup=popup
                ).add_to(fg)
        
        # Plot points (including converted centroids)
        else:
            point = geom.centroid if geom.geom_type in ['Polygon', 'MultiPolygon'] else geom
            folium.CircleMarker(
                location=[point.y, point.x],
                radius=4,
                popup=popup,
                color=color,
                fill=True,
                fillColor=color,
                fillOpacity=0.7
            ).add_to(fg)
    
    # 1. Adicionar todos os FeatureGroups ao mapa E pegar os nomes reais das variáveis JS
    fg_js_names = {}
    for tag, fg in feature_groups.items():
        fg.add_to(m)
        # Salva o nome interno bizarro que o Folium gera (ex: feature_group_8a3b...)
        fg_js_names[tag] = fg.get_name() 
    
    # Criar legenda HTML customizada com checkboxes e ícones
    legend_html = '''
    <div id="custom-legend" style="position: fixed; 
         bottom: 50px; right: 50px; width: 280px; height: auto; 
         background-color: white; border:2px solid grey; z-index:9999; font-size:14px;
         padding: 10px; border-radius: 5px; max-height: 70vh; overflow-y: auto;">
         <p style="margin: 0 0 15px 0; font-weight: bold; font-size: 16px;">Legend</p>
         <div id="legend-items"></div>
    </div>
    '''
    
    m.get_root().html.add_child(folium.Element(legend_html))
    
    map_var_name = m.get_name()
    tag_list = sorted(color_map.keys())
    
    # 2. Script hiper-robusto que funciona em qualquer ambiente
    script = f'''
    <script>
    // Usa um intervalo para verificar se o mapa já terminou de ser renderizado pelo Folium
    var checkExist = setInterval(function() {{
        var myMap = window["{map_var_name}"];
        
        // Se o mapa ainda não existe, não faz nada e tenta novamente em 100ms
        if (!myMap) return;
        
        // Se o mapa foi encontrado, paramos de checar e montamos a legenda!
        clearInterval(checkExist);
        
        var tagNames = {json.dumps(tag_list)};
        var colorMap = {json.dumps(color_map)};
        var fgVars = {json.dumps(fg_js_names)}; // <-- O segredo mágico que liga o Python ao JS
        
        var legendItems = document.getElementById('legend-items');
        
        tagNames.forEach(function(tag, index) {{
            var color = colorMap[tag];
            var item = document.createElement('div');
            item.style.margin = '8px 0';
            item.style.display = 'flex';
            item.style.alignItems = 'center';
            item.innerHTML = '<input type="checkbox" id="checkbox_' + index + '" checked style="margin-right: 8px; cursor: pointer; width: 16px; height: 16px;">' +
                             '<i style="background:' + color + '; width: 18px; height: 18px; display: inline-block; margin-right: 8px; border-radius: 50%; border: 1px solid #555;"></i>' +
                             '<label for="checkbox_' + index + '" style="margin: 0; cursor: pointer; user-select: none; flex-grow: 1;">' + tag + '</label>';
            legendItems.appendChild(item);
            
            // Controle dos Checkboxes
            var checkbox = document.getElementById('checkbox_' + index);
            checkbox.addEventListener('change', function() {{
                var layer = window[fgVars[tag]]; // Busca a camada exata pelo nome interno gerado
                if (layer) {{
                    if (checkbox.checked) {{
                        myMap.addLayer(layer);
                    }} else {{
                        myMap.removeLayer(layer);
                    }}
                }}
            }});
        }});
    }}, 100);
    </script>
    '''
    
    m.get_root().html.add_child(folium.Element(script))
    
    return m

# 4. Save functions
def save_html_map(m, key):
    """Save folium map to HTML"""
    output_file = f"docs/mapas/features_map_{key}.html"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    m.save(output_file)
    print(f"Map saved: {output_file}")
    return output_file

def save_parquet(features_points, save_path, key):
    """Save GeoDataFrame to parquet"""
    os.makedirs(save_path, exist_ok=True)
    output_file = os.path.join(save_path, f"features_{key}.parquet")
    features_points.to_parquet(output_file, compression="snappy")
    print(f"Parquet saved: {output_file}")
    return output_file

def save_pmtiles(features_points, save_path, key):
    """Save GeoDataFrame to PMTiles"""
    os.makedirs(save_path, exist_ok=True)
    output_file = os.path.join(save_path, f"features_{key}.pmtiles")
    
    if os.path.exists(output_file):
        os.remove(output_file)
        print(f"Deleted old {output_file}")
    
    features_points.to_file(
        output_file,
        driver="PMTiles",
        engine="pyogrio",
        encoding="utf-8",
        MINZOOM=0,
        MAXZOOM=14,
        NAME=f"layer_{key}"
    )
    print(f"PMTiles saved: {output_file}")
    return output_file

def process_key(key, tags, city_geom, save_path="Dados/Saída/", columns_to_show=None):
    """Process a single key end-to-end"""
    print(f"\n{'='*50}")
    print(f"Processing {key}...")
    print(f"{'='*50}")
    
    features_points = fetch_and_process_features(city_geom, key, tags)
    m = create_map(features_points, city_geom, key, columns_to_show)
    
    save_html_map(m, key)
    save_parquet(features_points, save_path, key)
    save_pmtiles(features_points, save_path, key)
    
    print(f"✓ Completed {key}\n")
    return features_points