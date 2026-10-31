"""FHIR R4 只读门面。

设计约束（与 docs/SPEC.md §3 对齐）：
- **只读**。不实现 write / update / delete，也不做 $everything 与版本化历史。
- **不引入 HAPI**。本服务只是资源的生产者，不是通用 FHIR 服务器；
  引入 HAPI 会把「映射问题」变成「部署一个中间件」，与本期边界不符。
- **无标准 code 时不编造**。测量类型没有可靠的 LOINC 映射，就用本地
  code system 并如实声明，绝不硬填一个看起来像 LOINC 的假编码。
"""

from app.fhir.errors import FhirError  # noqa: F401

FHIR_VERSION = "4.0.1"
FHIR_MEDIA_TYPE = "application/fhir+json"
