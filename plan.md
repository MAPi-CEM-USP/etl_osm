## Plan: Mandatory cd_mun + Posterior Merge Outputs

Refactor the ETL so each execution requires explicit cd_mun input (single code or user-provided list), never an implicit full-country run. Keep data exports grouped by theme for storage efficiency: Dados/Saída/{theme}/features_{cd_mun}.{parquet|pmtiles}. Keep docs map pages municipality-first for dropdown navigation: docs/mapas/{cd_mun}/{theme}/features_map.html. Add a posterior merge step (preferably in a new notebook) that creates consolidated files by theme, only for parquet and pmtiles.

**Steps**
1. Define execution contract in notebook: cd_mun must be explicitly provided as one code or a list (depends on none)
2. Filter IBGE geometries only for provided cd_mun values; remove any iteration over all ibge rows (depends on 1)
3. Update extract_features.py signatures so process_key() and save_files() require cd_mun (and theme_name for path routing) (depends on 1)
4. Add input validation: raise clear error when cd_mun is None/empty or not found in IBGE (depends on 2, 3)
5. Implement theme-first output paths for parquet/pmtiles: Dados/Saída/{theme}/features_{cd_mun}.{ext} (depends on 3)
6. Implement municipality-first docs HTML paths: docs/mapas/{cd_mun}/{theme}/features_map.html (depends on 3)
7. Refactor main notebook blocks so standard keys and custom groups run only for selected cd_mun values, passing cd_mun and theme_name (depends on 2, 3)
8. Add progress logging per requested cd_mun and theme, with optional continue-on-error behavior (parallel with 7)
9. Create a new posterior merge notebook (e.g., merge_outputs.ipynb) with function(s) to merge by theme only for parquet and pmtiles (depends on 5, 7)
10. In merge notebook, support both scope modes via parameter: merge_scope="selected" with selected_cd_mun list, or merge_scope="all" to scan municipality folders/files automatically (depends on 9)
11. Implement parquet merge pipeline: collect per-theme files for selected scope, normalize schema where needed, concatenate to one GeoDataFrame, write merged parquet (depends on 10)
12. Implement pmtiles merge pipeline by regeneration (not binary merge): use merged GeoDataFrame from step 11 and export a new merged pmtiles per theme with pyogrio (depends on 11)
13. Define merged output naming convention in merge notebook, e.g., Dados/Saída/merged/{theme}/features_merged.parquet and .pmtiles (depends on 11, 12)
14. Update docs/index discovery logic for municipality-first navigation (independent of merge output because merge is data-only) (parallel with 9)
15. Execute smoke tests with 1-2 cd_mun values, then run merge in both scope modes and verify artifacts (depends on 11, 12, 13)

**Relevant files**
- /home/gil/github/etl_osm/extract_features.py — require cd_mun/theme_name in process_key() and save_files(); validate inputs and path construction
- /home/gil/github/etl_osm/etl_features.ipynb — replace hardcoded municipality blocks with parameterized selected-cd_mun flow
- /home/gil/github/etl_osm/merge_outputs.ipynb (new) — posterior merge workflow for parquet + pmtiles with merge_scope parameter
- /home/gil/github/etl_osm/docs/index.html — municipality dropdown and theme filtering based on docs/mapas/{cd_mun}/{theme}
- /home/gil/github/etl_osm/README.md — document mandatory cd_mun run and posterior merge usage

**Verification**
1. Run ETL with one explicit cd_mun and confirm only that municipality outputs are produced
2. Run ETL with two explicit cd_mun values and confirm non-overwrite outputs per theme
3. Confirm docs outputs are created under docs/mapas/{cd_mun}/{theme}/features_map.html
4. In merge notebook, run merge_scope="selected" and verify merged parquet/pmtiles only include provided cd_mun values
5. In merge notebook, run merge_scope="all" and verify merged parquet/pmtiles include all discovered municipality outputs
6. Validate merged parquet row counts approximately equal sum of input municipal files per theme
7. Validate merged pmtiles files are generated successfully from merged GeoDataFrames
8. Validate missing/invalid cd_mun fails fast in ETL stage with clear error message

**Decisions**
- Accepted: cd_mun is mandatory for ETL processing
- Accepted: docs folder is municipality-first (`cd_mun/theme`)
- Accepted: posterior merge exists and is limited to parquet + pmtiles
- Accepted: merge supports both parameterized selected list and all-discovered modes
- Kept: data export path theme-first under Dados/Saída
- Excluded: full-country implicit ETL mode and docs generation from merged files

**Further Considerations**
1. PMTiles in this stack should be regenerated from merged GeoDataFrames; direct binary merge of .pmtiles files is not part of current toolchain
2. For reproducibility, persist a run manifest with list of input files used in each merged output
3. Normalize cd_mun to string at input and path levels to avoid type mismatches