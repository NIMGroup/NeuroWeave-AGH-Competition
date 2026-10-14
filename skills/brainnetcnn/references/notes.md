# brainnetcnn：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Architecture

```text
Dense connectivity matrix [B, 1, N, N]
  -> E2E convolution blocks
  -> E2N convolution
  -> N2G convolution
  -> fully connected prediction head
  -> class logits or one regression value
```

| Parameter | Default | Meaning |
|---|---:|---|
| `--e2e-channels` | 32 | E2E feature maps |
| `--e2n-channels` | 64 | E2N feature maps |
| `--n2g-channels` | 256 | graph-level representation |
| `--dropout` | 0.5 | prediction-head dropout |
| `--lr` | 0.001 | Adam learning rate |
| `--weight-decay` | 0.0005 | L2 regularization |
| `--kfold` | 5 | subject-level folds |

## Reference

- Kawahara J, Brown CJ, Miller SP, et al. BrainNetCNN: Convolutional neural
  networks for brain networks; towards predicting neurodevelopment.
  *NeuroImage*. 2017;146:1038-1049.
- Official implementation: https://github.com/jeremykawahara/brainnetcnn

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-31 14:24:19 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
