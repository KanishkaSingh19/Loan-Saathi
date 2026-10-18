**Voice model** (22 features, validation default rate 8.1%)

| Model | AUC | Brier | Log loss | Train time | Trees | Score 1 caller | Model size |
|---|---|---|---|---|---|---|---|
| LightGBM | 0.6889 | 0.0712 | 0.2630 | 14s | 298 | 7.3 ms | 1.1 MB |
| XGBoost | 0.6876 | 0.0713 | 0.2632 | 33s | 359 | 18.8 ms | 2.4 MB |
| CatBoost | 0.6883 | 0.0712 | 0.2632 | 435s | 1085 | 3.1 ms | 1.4 MB |
| Blend (average) | 0.6897 | 0.0712 | 0.2628 | | | | |

| Model | Low band: share / default rate | Medium | High |
|---|---|---|---|
| LightGBM | 35% / 3.4% | 47% / 8.0% | 18% / 17.1% |
| XGBoost | 35% / 3.5% | 46% / 8.0% | 18% / 17.1% |
| CatBoost | 29% / 3.2% | 51% / 7.4% | 20% / 16.8% |
| Blend (average) | 33% / 3.4% | 48% / 7.7% | 19% / 17.1% |

**Full model** (170 features, validation default rate 8.1%)

| Model | AUC | Brier | Log loss | Train time | Trees | Score 1 caller | Model size |
|---|---|---|---|---|---|---|---|
| LightGBM | 0.7866 | 0.0658 | 0.2368 | 54s | 469 | 15.4 ms | 1.7 MB |
| XGBoost | 0.7865 | 0.0657 | 0.2367 | 88s | 470 | 48.5 ms | 3.3 MB |
| CatBoost | 0.7896 | 0.0656 | 0.2360 | 992s | 2052 | 17.4 ms | 2.8 MB |
| Blend (average) | 0.7900 | 0.0654 | 0.2355 | | | | |

| Model | Low band: share / default rate | Medium | High |
|---|---|---|---|
| LightGBM | 53% / 2.5% | 28% / 8.1% | 20% / 23.1% |
| XGBoost | 53% / 2.5% | 27% / 8.1% | 20% / 23.2% |
| CatBoost | 53% / 2.5% | 28% / 7.9% | 19% / 23.4% |
| Blend (average) | 52% / 2.5% | 28% / 7.9% | 20% / 23.4% |
