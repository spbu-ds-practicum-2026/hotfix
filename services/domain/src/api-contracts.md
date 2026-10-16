# **HTTP API Контракты (Domain)**

**Статус:** Черновик для согласования (Неделя 1\)  
**Владелец:** Участник №3 (Backend/Domain)

# **1\. Турнирная таблица (Standings)**

`GET /api/v1/contests/{contestId}/standings`**Описание:** Возвращает актуальную турнирную таблицу соревнования. При включенном режиме freeze возвращает замороженный снимок.

**Ответ (Успех 200):**{

  "contestId": "UUID",

  "frozen": false,

  "rows": \[

    {

      "rank": 1,

      "teamId": "UUID",

      "solved": 2,

      "penalty": 70,

      "problems": \[

        {

          "problemId": "UUID",

          "attempts": 1,

          "accepted": true,

          "acceptedAt": "2026-10-15T10:20:00Z",

          "penalty": 20

        }

      \]

    }

  \]

}

# **2\. Управление заморозкой (Freeze)**

`POST /api/v1/contests/{contestId}/freeze`  
**Описание:** Включает режим freeze (заморозка турнирной таблицы).

`POST /api/v1/contests/{contestId}/unfreeze`  
**Описание:** Выключает режим freeze, обновляя таблицу до актуального состояния.

# **3\. Сущности соревнования (CRUD)**

`GET /api/v1/contests/{contestId}`  
**Описание:** Возвращает параметры соревнования (`startsAt`, `endsAt`, текущий статус isFrozen).

GET /api/v1/contests/{contestId}/problems  
Описание: Возвращает список задач для данного соревнования.