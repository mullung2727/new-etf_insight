import { test, expect } from "@playwright/test";
import { extractSlug, entryToChannel, channelToEntry } from "../lib/telegram-channels";

test("extractSlug: URL/bare 모두 slug 추출, 잘못된 입력 throw", () => {
  expect(extractSlug("https://t.me/s/companyreport")).toBe("companyreport");
  expect(extractSlug("t.me/s/getfeed")).toBe("getfeed");
  expect(extractSlug("  kimcharger ")).toBe("kimcharger");
  expect(() => extractSlug("https://example.com/foo")).toThrow();
  expect(() => extractSlug("공백 있음")).toThrow();
});

test("entryToChannel: feed_role→discovery, label 없으면 key", () => {
  expect(entryToChannel("getfeed", { source_url: "u", feed_role: "discovery_source" })).toEqual({
    key: "getfeed",
    url: "u",
    label: "getfeed",
    discovery: true,
  });
  const c = entryToChannel("companyreport", { source_url: "u", label: "컴리" });
  expect(c.discovery).toBe(false);
  expect(c.label).toBe("컴리");
});

test("channelToEntry: discovery/label 조건부 필드", () => {
  expect(channelToEntry({ key: "getfeed", discovery: true })).toEqual({
    source_url: "https://t.me/s/getfeed",
    feed_role: "discovery_source",
  });
  // label==key면 생략, discovery false면 feed_role 생략
  expect(channelToEntry({ key: "getfeed", label: "getfeed", discovery: false })).toEqual({
    source_url: "https://t.me/s/getfeed",
  });
  expect(channelToEntry({ key: "getfeed", label: "겟피드", discovery: false })).toEqual({
    source_url: "https://t.me/s/getfeed",
    label: "겟피드",
  });
});
