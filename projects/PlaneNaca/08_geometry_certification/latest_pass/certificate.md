# Сертификат геометрии RepairMach

- ID: `RMC-E7A21E0D0E2A4A15`
- Методика: `RM91-GEOMETRY-CERT-1`
- Вердикт: **PASS_WITH_DECLARED_EXCLUSIONS**
- Создан: 2026-09-09T02:06:57+03:00
- MASTER: `5d8ecd50e765853043050e029d2c5571b593ead411fef9f81fa7f0e9567ddef7`
- MASTER неизменён: **да**
- Пригодность для решателя: **да**
- Представлена полная геометрия: **нет**
- Требуется гибридная замена: **да**

Сертификат подтверждает прослеживаемость и пригодность геометрии для указанной постановки. Он не подтверждает аэродинамическую точность и не заменяет сеточную или экспериментальную верификацию.

## Область действия

- Mach: `[[0.0, 0.8], [1.2, 2.2]]`
- alpha: `[0.0, 5.0]` град
- beta: `0.0` град

## Backends

| Backend | Пригоден | Постановка |
|---|---:|---|
| vspaero | да | lifting |
| machline | нет | None |
| parasite_drag | да | full_geometry |
| hybrid | да | declared_sources_required |

## Преобразования

Преобразования геометрии не потребовались (`PASS_NATIVE`).

## Исключения

- Fuselage: mixed VSPAERO probe rejected; lifting-only twin accepted

## Диагностика

| Код | Уровень | Компонент | Сообщение |
|---|---|---|---|
| `GEO-VSP-001` | WARNING |  | VSPAERO (mixed) сообщил диагностические особенности сетки; результаты допускаются только по конечности и сеточной сходимости |
| `GEO-VSP-002` | WARNING |  | Смешанная постановка VSPAERO не прошла квалификацию; принят отдельный несущий двойник с явной гибридной заменой толстых тел |

## Артефакты

- Семантический аудит: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_020500_Plane_Naca_final_PlaneNaca_STABLE_M08\semantic_audit.json`
- Исходная диагностика: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_020500_Plane_Naca_final_PlaneNaca_STABLE_M08\native_diagnostics.json`
- План: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_020500_Plane_Naca_final_PlaneNaca_STABLE_M08\transformation_plan.json`
- Отклонения: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_020500_Plane_Naca_final_PlaneNaca_STABLE_M08\geometry_deltas.json`
