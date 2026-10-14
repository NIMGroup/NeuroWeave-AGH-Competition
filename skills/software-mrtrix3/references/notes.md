# MRtrix3：扩散数据与连接组注意事项

## 数据条件与降噪

核对 DWI、梯度表、b 值、梯度坐标约定及掩膜的对应关系。运动校正后的梯度不能直接用未经相应旋转的原始梯度替代。

MP-PCA 降噪应在会引入空间插值的预处理之前进行。检查残差是否出现明显解剖结构，并结合数据采集条件评价降噪效果。[MRtrix3 官方降噪说明](https://mrtrix.readthedocs.io/en/latest/dwi_preprocessing/denoising.html)

## 追踪与连接组

ACT 的组织分割、FOD 和分区需要正确对齐。纤维追踪是对路径的估计，纤维条数不等于真实轴突数；原始条数、过滤后的条数与加权连接强度也不是同一种量。

构建连接矩阵时检查分区标签与矩阵行列的映射，保存节点顺序及端点分配规则。分区与纤维不对齐会影响连接结果。[MRtrix3 官方连接组说明](https://mrtrix.readthedocs.io/en/latest/quantitative_structural_connectivity/structural_connectome.html)

使用 SIFT2 权重时，权重必须对应产生它们的纤维文件及顺序；不能与重新排序或过滤后的另一份纤维直接混用。

## 当前能力边界

已存在的 FOD、5TT、纤维和权重应根据本次目标复用；哪些输入是必须的，以对应 Method/Pipeline 的契约为准。原生软件帮助中的全部命令和选项不一定已接入，不能据此拼接额外 Shell 操作。

## 来源

依据当前 MRtrix3 Method 契约、DWI 参考资料及上述官方文档整理；新增说明不改变原软件许可与实现归属。
