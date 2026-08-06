import TurfRoster from "@/components/TurfRoster";

export default async function TurfPage({
  params,
}: {
  params: Promise<{ turfId: string }>;
}) {
  const { turfId } = await params;
  return <TurfRoster turfId={turfId} />;
}
