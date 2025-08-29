export type OutletCsvRow = {
  crawl_ts: string;
  locale: string;
  category_path: string | null;
  name: string;
  sku: string | null;
  product_url: string;
  color: string | null;
  list_price: string | null;
  sale_price: string | null;
  discount: string | null;
  image_url: string | null;
  inventory_amount: number | string | null;
  size_quantities?: string | null;
  sizes_all: string | null;
  sizes_in_stock: string | null;
  sizes_out_of_stock: string | null;
  num_sizes_in_stock: number | string | null;
  hash_key: string;
  source: string;
};

export type NormalizedInventoryRecord = {
  name: string;
  color: string;
  size: string;
  inStock: boolean;
  productUrl?: string;
  imageUrl?: string;
  inventoryAmount?: number;
  sizeQty?: number;
};

export type UploadRow = {
  item: string; // e.g., item number or product name
  color?: string | null;
  size?: string | null;
};

export type MatchStatus =
  | "IN_STOCK"
  | "OUT_OF_STOCK"
  | "VARIANT_NOT_FOUND"
  | "ITEM_NOT_FOUND";

export type MatchResult = {
  query: UploadRow;
  status: MatchStatus;
  matches: NormalizedInventoryRecord[];
};



