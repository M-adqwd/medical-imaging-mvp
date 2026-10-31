# ADR-001 · 影像存储选型 Orthanc 而非 DCM4CHEE

- **状态**：已接受
- **日期**：2026-10-27

## 背景

需要一套 DICOMweb（QIDO-RS / WADO-RS）服务，作为影像检索与取回的事实源。
候选为 Orthanc 与 DCM4CHEE。

## 决策

采用 **Orthanc**，通过 `/dicomweb` 反代暴露 QIDO/WADO。

## 理由

1. 首期只需要 DICOMweb 的读路径（QIDO 检索 + WADO 取实例），Orthanc 单进程即可满足，
   DCM4CHEE 的 LDAP / 归档分层 / 多租户能力在 MVP 阶段是用不上的复杂度。
2. 本地可一键拉起，验证链路短 —— 当前已实测 128 个实例全部可检索、可取回。
3. 系统层不自定义 DICOMweb 字段（契约 §5.5），因此不需要 DCM4CHEE 的可扩展元数据模型。

## 后果

- 生产化阶段若需要归档分层 / 长期存储策略 / 多院区租户，需重新评估迁往 DCM4CHEE。
  迁移面被限制在 `/dicomweb` 前缀之后，业务代码不感知底层实现。
- Healthcheck 应暴露 `dicomweb_mode`，让"本地降级 / 真实 Orthanc"在界面上可区分，
  否则排障时无法判断数据来自哪里。
