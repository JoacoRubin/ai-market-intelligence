/* ============================================================================
   Esquema de la base transaccional/analítica.

   Decisiones que valen la pena explicar:

   - Claves de negocio como VARCHAR ('P001', 'O000123'). En un sistema real
     usaría identidades numéricas; acá los IDs legibles hacen que las trazas del
     agente y los reportes se lean sin tener que resolver joins mentalmente.

   - DECIMAL para dinero, nunca FLOAT. Un FLOAT acumula error de redondeo y
     produce revenues que no cierran contra la suma de sus líneas. Un KPI que no
     cuadra por centavos destruye la confianza en todo el informe.

   - Índices orientados a las consultas reales del agente: casi todas filtran
     por producto y rango de fechas.

   - Sin claves foráneas ON DELETE CASCADE: los datos son inmutables una vez
     cargados. Si hay que regenerar, se trunca y se vuelve a cargar.
   ============================================================================ */

IF DB_ID('ami') IS NULL
    EXEC('CREATE DATABASE ami');
GO

USE ami;
GO

/* --- Limpieza idempotente: permite recargar sin recrear el contenedor ----- */
IF OBJECT_ID('dbo.returns', 'U')     IS NOT NULL DROP TABLE dbo.returns;
IF OBJECT_ID('dbo.order_items', 'U') IS NOT NULL DROP TABLE dbo.order_items;
IF OBJECT_ID('dbo.orders', 'U')      IS NOT NULL DROP TABLE dbo.orders;
IF OBJECT_ID('dbo.inventory', 'U')   IS NOT NULL DROP TABLE dbo.inventory;
IF OBJECT_ID('dbo.campaigns', 'U')   IS NOT NULL DROP TABLE dbo.campaigns;
IF OBJECT_ID('dbo.ground_truth','U') IS NOT NULL DROP TABLE dbo.ground_truth;
IF OBJECT_ID('dbo.products', 'U')    IS NOT NULL DROP TABLE dbo.products;
IF OBJECT_ID('dbo.customers', 'U')   IS NOT NULL DROP TABLE dbo.customers;
GO

/* --- Catálogo ------------------------------------------------------------- */
CREATE TABLE dbo.products (
    id          VARCHAR(10)   NOT NULL PRIMARY KEY,
    brand       VARCHAR(50)   NOT NULL,
    category    VARCHAR(50)   NOT NULL,
    price       DECIMAL(12,2) NOT NULL,
    cost        DECIMAL(12,2) NOT NULL,
    launch_date DATE          NOT NULL,
    CONSTRAINT ck_products_precio_positivo CHECK (price > 0),
    CONSTRAINT ck_products_margen_positivo CHECK (price > cost)
);
GO

CREATE TABLE dbo.customers (
    id         VARCHAR(10)  NOT NULL PRIMARY KEY,
    segment    VARCHAR(20)  NOT NULL,
    region     VARCHAR(30)  NOT NULL,
    created_at DATETIME2(0) NOT NULL
);
GO

/* --- Transaccional -------------------------------------------------------- */
CREATE TABLE dbo.orders (
    id          VARCHAR(12)  NOT NULL PRIMARY KEY,
    customer_id VARCHAR(10)  NOT NULL,
    created_at  DATETIME2(0) NOT NULL,
    channel     VARCHAR(20)  NOT NULL,
    status      VARCHAR(20)  NOT NULL,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES dbo.customers(id)
);
GO

CREATE TABLE dbo.order_items (
    id         VARCHAR(14)   NOT NULL PRIMARY KEY,
    order_id   VARCHAR(12)   NOT NULL,
    product_id VARCHAR(10)   NOT NULL,
    quantity   INT           NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    CONSTRAINT fk_items_order   FOREIGN KEY (order_id)   REFERENCES dbo.orders(id),
    CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES dbo.products(id),
    CONSTRAINT ck_items_cantidad_positiva CHECK (quantity > 0),
    CONSTRAINT ck_items_precio_positivo   CHECK (unit_price > 0),
    -- Un producto no puede repetirse dentro de la misma orden: la restricción
    -- vive en la base, no solo en el generador. Los datos se defienden solos.
    CONSTRAINT uq_items_orden_producto UNIQUE (order_id, product_id)
);
GO

CREATE TABLE dbo.returns (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    order_item_id VARCHAR(14)  NOT NULL,
    reason        VARCHAR(80)  NOT NULL,
    created_at    DATETIME2(0) NOT NULL,
    CONSTRAINT fk_returns_item FOREIGN KEY (order_item_id)
        REFERENCES dbo.order_items(id)
);
GO

CREATE TABLE dbo.inventory (
    product_id VARCHAR(10) NOT NULL,
    date       DATE        NOT NULL,
    stock      INT         NOT NULL,
    CONSTRAINT pk_inventory PRIMARY KEY (product_id, date),
    CONSTRAINT fk_inventory_product FOREIGN KEY (product_id)
        REFERENCES dbo.products(id),
    CONSTRAINT ck_inventory_stock_no_negativo CHECK (stock >= 0)
);
GO

CREATE TABLE dbo.campaigns (
    id         INT IDENTITY(1,1) PRIMARY KEY,
    product_id VARCHAR(10)   NOT NULL,
    start_date DATE          NOT NULL,
    end_date   DATE          NOT NULL,
    spend      DECIMAL(12,2) NOT NULL,
    discount   DECIMAL(5,3)  NOT NULL,
    CONSTRAINT fk_campaigns_product FOREIGN KEY (product_id)
        REFERENCES dbo.products(id),
    CONSTRAINT ck_campaigns_rango CHECK (end_date >= start_date)
);
GO

/* --- Ground truth ---------------------------------------------------------
   No es una tabla de negocio: registra los eventos anómalos sembrados a
   propósito en el dataset. Existe para poder evaluar la detección.
   El agente NO tiene acceso a esta tabla — sería hacer trampa.
   -------------------------------------------------------------------------- */
CREATE TABLE dbo.ground_truth (
    id          INT IDENTITY(1,1) PRIMARY KEY,
    tipo        VARCHAR(40)   NOT NULL,
    product_id  VARCHAR(10)   NOT NULL,
    fecha       DATE          NOT NULL,
    magnitud    DECIMAL(10,2) NULL,
    descripcion VARCHAR(300)  NOT NULL,
    CONSTRAINT fk_gt_product FOREIGN KEY (product_id) REFERENCES dbo.products(id)
);
GO

/* --- Índices --------------------------------------------------------------
   Modelados sobre las consultas reales del agente: comparar productos en un
   rango de fechas. El INCLUDE evita el lookup a la tabla base en los KPIs.
   -------------------------------------------------------------------------- */
CREATE INDEX ix_orders_created_at   ON dbo.orders(created_at) INCLUDE (status, channel);
CREATE INDEX ix_items_product       ON dbo.order_items(product_id) INCLUDE (quantity, unit_price, order_id);
CREATE INDEX ix_items_order         ON dbo.order_items(order_id);
CREATE INDEX ix_returns_created_at  ON dbo.returns(created_at) INCLUDE (order_item_id);
CREATE INDEX ix_inventory_date      ON dbo.inventory(date) INCLUDE (stock);
CREATE INDEX ix_campaigns_product   ON dbo.campaigns(product_id, start_date, end_date);
GO

PRINT 'Esquema ami creado correctamente.';
GO
