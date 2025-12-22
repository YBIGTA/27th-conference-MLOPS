cd /Users/jaehun/Desktop/Github\ Projects/27th-conference-MLOPS
set -a && source infra/database/.env && set +a

python -m infra.database.scripts.prefill_news_data \
  --bucket ybigta-mlops-landing-zone-324037321745 \
  --prefix ExtContent/news_data/ \
  --start 2025-12-11T00:00:00Z \
  --dump-csv ./news_prefill.csv
