# etl_osm

### 🛠 Development Setup
This project uses a Python virtual environment to manage dependencies. Follow these steps to get your local environment running.
#### 1. Initialize the Virtual Environment
Create a new environment folder named .venv in the root directory:
```Bash
python -m venv .venv
```
#### 2. Activate the Environment
You must activate the environment every time you open a new terminal session.
- Windows (Command Prompt):
```DOS
.venv\Scripts\activate
```
- Windows (PowerShell):
```PowerShell
.\.venv\Scripts\Activate.ps1
```
- macOS / Linux:
```Bashsource 
source .venv/bin/activate
```
#### 3. Install Dependencies
Once activated |  install the required packages defined in `requirements.txt`:
```Bash
pip install -r requirements.txt
```
##### 💡 Useful `venv` Commands

|Action | Command | Description |
|---|---|---|
Deactivate | `deactivate` | Exit the virtual environment and return to global Python.
Check Packages | `pip list` | See all libraries currently installed in the environment.
Export Deps | `pip freeze > requirements.txt` | Save your current environment state to the requirements file.
Check Version | `python --version` | Confirms you are using the correct Python executable.

**Note**: Do not commit the `.venv` folder to GitHub. Ensure `.venv/` is added to your `.gitignore` file.

### ETL Output Conventions

The ETL now requires explicit `cd_mun` selection in the notebook flow.

- Data outputs (theme-first):
	- `Dados/Saída/{theme}/features_{cd_mun}.parquet`
	- `Dados/Saída/{theme}/features_{cd_mun}.pmtiles`
- Docs maps (municipality-first):
	- `docs/mapas/{cd_mun}/{theme}/features_map.html`
- Docs manifest for index dropdown:
	- `docs/mapas/manifest.json`

### Posterior Merge

Use `merge_outputs.ipynb` after municipal runs to build merged outputs per theme.

- Merge scope modes:
	- `merge_scope = "selected"` and provide `selected_cd_mun`
	- `merge_scope = "all"` to merge all municipal files found
- Merged outputs:
	- `Dados/Saída/merged/{theme}/features_merged.parquet`
	- `Dados/Saída/merged/{theme}/features_merged.pmtiles`