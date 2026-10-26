# California WARN layoff collector

A small Python pipeline that:

1. Opens the official EDD WARN page
2. Downloads the latest WARN Excel report
3. Cleans it with pandas
4. Saves `ca_warn_data.csv`
5. Prints a short console summary

You do not need to write any code to run this. Copy the commands below.

## One-time setup (macOS)

This Mac currently does not have Apple’s Command Line Tools, which also provide `python3` and `git`. Install them first:

```bash
xcode-select --install
```

Finish the installer window, then close and reopen Terminal. Check Python:

```bash
python3 --version
```

You want Python 3.10 or newer.

Then create a virtual environment and install libraries:

```bash
cd ~/ca-warn-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the collector

```bash
cd ~/ca-warn-tracker
source .venv/bin/activate
python collect_ca_warn.py
```

When it finishes you will have:

- `data/warn_report_latest.xlsx` — the file from EDD
- `ca_warn_data.csv` — cleaned table you can open in Excel or Numbers

The CSV columns are: `notice_date`, `effective_date`, `processed_date`, `company`, `county`, `address`, `layoff_or_closure`, `employees`, `industry`.

## Automated economic engine

FRED needs a free API key in `.env` (`FRED_API_KEY=...`). Copy `.env.example` if you do not have one yet: https://fredaccount.stlouisfed.org/apikeys

Install everything, refresh data, and open the dashboard:

```bash
cd ~/ca-warn-tracker
source .venv/bin/activate
pip install -r requirements.txt
python run_all.py
streamlit run app.py
```

`run_all.py` downloads California, Texas, and New York WARN notices plus FRED series, writes `us_warn_data.csv`, and prints a Macro Stress Index from 0 to 100%.

`app.py` is a dark fintech dashboard: stress gauge, executive takeaway, CA/TX filters, Plotly charts, and a searchable layoff table. Streamlit will print a local URL (usually http://localhost:8501).

Thresholds used in the score:

- Yield curve (T10Y2Y): inversion at `<= 0`
- Sahm Rule: `>= 0.50`
- High-yield OAS (BAMLH0A0HYM2): `> 5.0%` (500 bps)
- Consumer sentiment (UMCSENT): `<= 70`
- Initial claims (ICSA, 4-week average): `>= 300,000`
- Housing: months' supply of new homes (MSACSR) `> 8.0` **or** building permits (PERMIT) down more than 10% YoY
- Vehicle sales (TOTALSA): `< 15.0` million (SAAR)
- Personal saving rate (PSAVERT): `< 3.5%`

## Data sources

- [EDD WARN](https://edd.ca.gov/en/jobs_and_training/layoff_services_warn)
- [TWC WARN](https://www.twc.texas.gov/data-reports/warn-notice)
- [NYS DOL WARN dashboard](https://dol.ny.gov/warn-dashboard)
- [FRED](https://fred.stlouisfed.org/) series T10Y2Y, SAHMREALTIME, BAMLH0A0HYM2, UMCSENT, ICSA, PERMIT, MSACSR, TOTALSA, PSAVERT
