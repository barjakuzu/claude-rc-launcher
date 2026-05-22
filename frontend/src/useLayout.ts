import { useEffect, useState } from 'react';

export interface Layout {
  width: number;
  mobile: boolean;
  tablet: boolean;
  desktop: boolean;
}

export function useLayout(): Layout {
  const [w, setW] = useState<number>(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return { width: w, mobile: w < 720, tablet: w >= 720 && w < 1100, desktop: w >= 1100 };
}
