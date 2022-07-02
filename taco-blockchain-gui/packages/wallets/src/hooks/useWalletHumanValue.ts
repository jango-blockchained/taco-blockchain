import { useMemo } from 'react';
import type { Wallet } from '@taco/api';
import { WalletType } from '@taco/api';
import BigNumber from 'bignumber.js';
import { mojoToCATLocaleString, mojoToTacoLocaleString, useLocale } from '@taco/core';

export default function useWalletHumanValue(wallet: Wallet, value?: string | number | BigNumber, unit?: string): string {
  const [locale] = useLocale();
  
  return useMemo(() => {
    if (wallet && value !== undefined) {
      const localisedValue = wallet.type === WalletType.CAT
        ? mojoToCATLocaleString(value, locale)
        : mojoToTacoLocaleString(value, locale);

      return `${localisedValue} ${unit}`;
    }

    return '';
  }, [wallet, value, unit, locale]);
}
