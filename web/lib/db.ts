import { DuckDBInstance } from "@duckdb/node-api";
import path from "path";

const DB_PATH = path.resolve(
  process.cwd(),
  process.env.ETF_DB_PATH ?? "../etl/db/etf_insight.duckdb"
);

const g = globalThis as { _etfDb?: DuckDBInstance };

async function getInstance(): Promise<DuckDBInstance> {
  if (!g._etfDb) {
    g._etfDb = await DuckDBInstance.create(DB_PATH, {
      access_mode: "READ_ONLY",
    });
  }
  return g._etfDb;
}

export async function query<T = Record<string, unknown>>(
  sql: string,
  params?: Record<string, unknown>
): Promise<T[]> {
  const conn = await (await getInstance()).connect();
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const reader = await conn.runAndReadAll(sql, params as any);
    return reader.getRowObjectsJson() as T[];
  } finally {
    conn.closeSync();
  }
}
