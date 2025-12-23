import argparse
import json
import os
import random
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from torch.utils.data import DataLoader, Dataset


TARGET_COLS = [
    "trend_return_pct",
    "mean_revert_return_pct",
    "breakout_return_pct",
    "scalper_return_pct",
    "long_hold_return_pct",
    "short_hold_return_pct",
]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class ReturnDataset(Dataset):
    def __init__(self, features: np.ndarray, targets: np.ndarray):
        self.x = torch.from_numpy(features.astype(np.float32))
        self.y = torch.from_numpy(targets.astype(np.float32))

    def __len__(self) -> int:
        return self.x.shape[0]

    def __getitem__(self, idx: int):
        return self.x[idx], self.y[idx]


class MLP(nn.Module):
    def __init__(self, input_dim: int, hidden_dims: List[int], output_dim: int, dropout: float, use_layernorm: bool):
        super().__init__()
        layers: List[nn.Module] = []
        last_dim = input_dim
        for dim in hidden_dims:
            layers.append(nn.Linear(last_dim, dim))
            if use_layernorm:
                layers.append(nn.LayerNorm(dim))
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            last_dim = dim
        layers.append(nn.Linear(last_dim, output_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def time_split(df: pd.DataFrame, train_ratio=0.7, val_ratio=0.1) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("ts").reset_index(drop=True)
    n = len(df)
    n_train = max(1, int(n * train_ratio))
    n_val = max(1, int(n * val_ratio))
    n_train = min(n_train, n - n_val - 1)
    train_df = df.iloc[:n_train]
    val_df = df.iloc[n_train : n_train + n_val]
    test_df = df.iloc[n_train + n_val :]
    return train_df, val_df, test_df


def load_dataset(path: Path) -> pd.DataFrame:
    if path.is_dir():
        file_path = path / "train.parquet"
    else:
        file_path = path
    if not file_path.exists():
        raise FileNotFoundError(f"데이터 파일을 찾을 수 없습니다: {file_path}")
    df = pd.read_parquet(file_path)
    if "features" not in df.columns:
        raise ValueError("features 컬럼이 없습니다.")
    df["ts"] = pd.to_datetime(df.get("ts"), utc=True, errors="coerce")
    df = df.dropna(subset=["ts"])
    return df


def make_arrays(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    feats = np.stack(df["features"].apply(np.asarray).to_list()).astype(np.float32)
    targets = df[TARGET_COLS].astype(np.float32).to_numpy()
    return feats, targets


def evaluate(model: nn.Module, loader: DataLoader, device: torch.device) -> Dict[str, float]:
    model.eval()
    ys: List[np.ndarray] = []
    preds: List[np.ndarray] = []
    with torch.no_grad():
        for xb, yb in loader:
            xb = xb.to(device)
            yb = yb.to(device)
            out = model(xb)
            ys.append(yb.cpu().numpy())
            preds.append(out.cpu().numpy())
    y_true = np.vstack(ys)
    y_pred = np.vstack(preds)
    return {
        "mse": float(mean_squared_error(y_true, y_pred)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


def train_loop(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    lr: float,
    weight_decay: float,
    epochs: int,
    patience: int,
) -> Tuple[nn.Module, Dict[str, float], int]:
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    criterion = nn.MSELoss()
    best_state = None
    best_val = float("inf")
    best_epoch = -1
    patience_left = patience

    for epoch in range(1, epochs + 1):
        model.train()
        running = 0.0
        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)
            opt.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            opt.step()
            running += loss.item() * xb.size(0)

        val_metrics = evaluate(model, val_loader, device)
        val_loss = val_metrics["mse"]
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.cpu() for k, v in model.state_dict().items()}
            best_epoch = epoch
            patience_left = patience
        else:
            patience_left -= 1
            if patience_left <= 0:
                break

    if best_state:
        model.load_state_dict(best_state)
    return model, {"best_val_mse": best_val}, best_epoch


def save_artifacts(model: nn.Module, metadata: Dict[str, any], model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_dir / "model.pth")
    with (model_dir / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SageMaker용 MLP 학습 스크립트")
    parser.add_argument("--train-path", type=str, default=os.getenv("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train"))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--use-layernorm", action="store_true", default=False)
    parser.add_argument("--hidden-dims", type=str, default="256,128,64", help="쉼표로 구분된 히든 레이어 크기")
    parser.add_argument("--train-uri", type=str, default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    df = load_dataset(Path(args.train_path))
    if len(df) < 3:
        raise ValueError("학습 데이터가 너무 적습니다(3개 미만).")
    feats, targets = make_arrays(df)
    train_df, val_df, test_df = time_split(df)
    x_train, y_train = make_arrays(train_df)
    x_val, y_val = make_arrays(val_df)
    x_test, y_test = make_arrays(test_df)

    train_loader = DataLoader(ReturnDataset(x_train, y_train), batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(ReturnDataset(x_val, y_val), batch_size=args.batch_size, shuffle=False)
    test_loader = DataLoader(ReturnDataset(x_test, y_test), batch_size=args.batch_size, shuffle=False)

    hidden_dims = [int(x) for x in args.hidden_dims.split(",") if x.strip()]
    model = MLP(
        input_dim=feats.shape[1],
        hidden_dims=hidden_dims,
        output_dim=len(TARGET_COLS),
        dropout=args.dropout,
        use_layernorm=args.use_layernorm,
    ).to(device)

    model, best_info, best_epoch = train_loop(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        device=device,
        lr=args.lr,
        weight_decay=args.weight_decay,
        epochs=args.epochs,
        patience=args.patience,
    )

    test_metrics = evaluate(model, test_loader, device)
    metrics = {
        "best_epoch": best_epoch,
        "best_val_mse": best_info["best_val_mse"],
        "test_mse": test_metrics["mse"],
        "test_mae": test_metrics["mae"],
        "test_r2": test_metrics["r2"],
        "train_samples": int(len(train_df)),
        "val_samples": int(len(val_df)),
        "test_samples": int(len(test_df)),
        "train_uri": args.train_uri,
    }

    model_dir = Path(os.getenv("SM_MODEL_DIR", "/opt/ml/model"))
    output_dir = Path(os.getenv("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data"))
    save_artifacts(
        model,
        metadata={
            "metrics": metrics,
            "target_cols": TARGET_COLS,
            "hidden_dims": hidden_dims,
            "dropout": args.dropout,
            "use_layernorm": args.use_layernorm,
            "feature_dim": feats.shape[1],
        },
        model_dir=model_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
