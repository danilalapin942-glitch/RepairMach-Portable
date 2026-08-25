RepairMach Portable Beta
========================

Цель
----
Это бета-оболочка, где MachLine работает как расчётный двигатель,
а пользователь взаимодействует с RepairMach.

Проверяемый сценарий:
- на другом компьютере НЕ установлен Python;
- на другом компьютере НЕ установлен MachLine;
- RepairMach запускается из собственной папки;
- MachLine запускается из engines/MachLine;
- Python запускается из runtime/python.

Что нужно добавить вручную
--------------------------
1) Portable Python:
   положить python.exe и файлы embedded Python в:
   runtime/python/

   Должен существовать файл:
   runtime/python/python.exe

2) MachLine:
   положить рабочий machline.exe и все нужные DLL/файлы в:
   engines/MachLine/

   Должен существовать файл:
   engines/MachLine/machline.exe

Запуск
------
Запустить:
RepairMach.bat

Меню
----
[1] Проверить среду
[2] Создать проект
[3] Выбрать проект
[4] Импортировать TRI
[5] Создать MachLine JSON
[6] Запустить MachLine
[7] Открыть папку проекта

Важное ограничение beta
-----------------------
В эту бета пока не встроены repair_v6 и batch_v7.
Она проверяет главный принцип:
RepairMach может работать как переносимая оболочка и запускать MachLine
без установленного Python и без установленного MachLine.

После успешного теста следующим шагом встраиваем:
- repair_v6_project.py;
- batch_machline_study.py;
- автоматическую починку под каждый режим;
- сбор CSV и dashboard.html.
