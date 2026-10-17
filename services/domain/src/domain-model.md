1. Базовые сущности и связи
     
   1.1. Contest (Соревнование)  
     
    contestId (UUID)  
     
    name (VARCHAR)  
     
    startsAt (TIMESTAMPTZ)  
     
    endsAt (TIMESTAMPTZ)  
     
    isFrozen (BOOLEAN, DEFAULT false)  
     
    Ограничение: Отправки (submissions), полученные до startsAt или после endsAt, должны отклоняться на уровне API с ошибкой CONTEST\_NOT\_OPEN.  
     
   1.2. Team (Команда)  
     
    teamId (UUID)  
     
    contestId (UUID) — привязка к соревнованию  
     
    name (VARCHAR)  
     
    Ограничение: Команда может участвовать только в том соревновании, к которому привязана. Ссылка на несуществующую команду возвращает TEAM\_NOT\_FOUND.  
     
   1.3. Problem (Задача)  
     
    problemId (UUID)  
     
    contestId (UUID) — привязка к соревнованию  
     
    code (VARCHAR) — буквенный код внутри соревнования (например, "A", "B")  
     
    title (VARCHAR)  
     
    timeLimitMs (INT)  
     
    memoryLimitKb (INT)  
     
    testConfigRef (VARCHAR)  
     
    Ограничение: Код задачи (code) обязан быть уникальным внутри одного contestId. Ссылка на несуществующую задачу возвращает PROBLEM\_NOT\_FOUND.  
     
   1.4. Standings (Турнирная таблица)  
     
    Ответ для Участника №1 (БД): Требуется отдельная таблица для кэширования рейтинга, чтобы не вычислять его на лету.  
     
    contest\_id (UUID, PRIMARY KEY) — привязка к соревнованию  
     
    is\_frozen (BOOLEAN) — статус заморозки (для быстрого чтения)  
     
    rows (JSONB) — массив актуальных результатов команд (решенные задачи, штрафное время)  
     
    last\_event\_sequence (BIGINT) — последнее обработанное событие (для дедупликации RabbitMQ)  
     
    updated\_at (TIMESTAMPTZ) — время последнего обновления  
     
2. Связь со смежными компонентами  
     
   Все API-ответы должны использовать camelCase для ключей (например, contestId), несмотря на snake\_case в базе данных (contest\_id).  
     
   Сервис проверки (worker) должен получать лимиты (timeLimitMs, memoryLimitKb) и testConfigRef из сущности Problem для отправки их в Judge0.