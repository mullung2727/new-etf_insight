import { notFound } from "next/navigation";
import { getEtfDetail, getEtfHoldings } from "@/lib/queries";
import { EtfDetailView } from "@/components/etf-detail";

interface PageProps {
  params: Promise<{ etf_key: string }>;
}

export default async function EtfDetailPage({ params }: PageProps) {
  const { etf_key } = await params;
  const [detail, holdings] = await Promise.all([
    getEtfDetail(etf_key),
    getEtfHoldings(etf_key),
  ]);

  if (!detail) notFound();

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8">
      <EtfDetailView detail={detail} holdings={holdings} />
    </div>
  );
}
