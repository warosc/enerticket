import type { ReactNode } from 'react';
export const metadata = { title: 'Energuate Service Desk', description: 'Gestión corporativa de tickets' };
export default function RootLayout({children}:{children:ReactNode}) {return <html lang="es"><body style={{margin:0,fontFamily:'Arial, sans-serif',background:'#f3f6fa',color:'#14243a'}}>{children}</body></html>;}
