import Head from 'next/head';
import OrionChat from '@/features/orion/OrionChat';

export default function OrionPage() {
  return <><Head><title>ORION | MMRF Virtual Lab</title><meta name="description" content="Evidence-linked Multiple Myeloma research in a synthetic sandbox" /></Head><OrionChat /></>;
}
