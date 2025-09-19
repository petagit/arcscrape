-- CreateTable
CREATE TABLE "variants" (
    "hash_key" TEXT NOT NULL PRIMARY KEY,
    "product_url" TEXT NOT NULL,
    "color" TEXT NOT NULL,
    "name" TEXT,
    "image_url" TEXT,
    "first_seen_at" DATETIME NOT NULL,
    "last_seen_at" DATETIME NOT NULL,
    "ever_in_stock" BOOLEAN NOT NULL DEFAULT false
);

-- CreateTable
CREATE TABLE "observations" (
    "obs_id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "run_id" TEXT NOT NULL,
    "hash_key" TEXT NOT NULL,
    "crawl_ts" DATETIME NOT NULL,
    "num_sizes_in_stock" INTEGER NOT NULL,
    "sizes_in_stock" TEXT NOT NULL,
    "sizes_all" TEXT NOT NULL,
    "list_price" TEXT,
    "sale_price" TEXT,
    "discount" TEXT,
    CONSTRAINT "observations_hash_key_fkey" FOREIGN KEY ("hash_key") REFERENCES "variants" ("hash_key") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateIndex
CREATE INDEX "observations_hash_ts" ON "observations"("hash_key", "crawl_ts");
