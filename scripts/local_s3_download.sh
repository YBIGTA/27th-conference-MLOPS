cd /Users/jaehun/Desktop/Github\ Projects/27th-conference-MLOPS
set -a && source infra/database/.env && set +a

python -m infra.database.scripts.prefill_price_1s_rest \
  --bucket ybigta-mlops-landing-zone-324037321745 \
  --prefix Binance/BTCUSDT/ \
  --dump-csv ./price_1s.csv \
  --flush-every-hours 1 \
  --skip-upload
