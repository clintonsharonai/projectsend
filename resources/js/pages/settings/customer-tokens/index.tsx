import { Head, Link, router } from '@inertiajs/react';
import { useState } from 'react';

import { ConfirmDialog } from '@/components/confirm-dialog';
import HeadingSmall from '@/components/heading-small';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useFormatDate } from '@/hooks/use-format-date';
import { useTranslation } from '@/hooks/use-translation';
import AppLayout from '@/layouts/app-layout';
import SettingsLayout from '@/layouts/settings/layout';
import { type BreadcrumbItem } from '@/types';

interface CustomerToken {
    id: string;
    name: string;
    abilities: string[];
    last_used_at: string | null;
    expires_at: string | null;
    expired: boolean;
    created_at: string | null;
}

interface Props {
    tokens: CustomerToken[];
    created_token: { name: string; plain_text: string } | null;
}

export default function CustomerTokensIndex({ tokens, created_token }: Props) {
    const { t } = useTranslation();
    const { date } = useFormatDate();
    const [copied, setCopied] = useState(false);

    const breadcrumbs: BreadcrumbItem[] = [{ title: t('Customer tokens'), href: '/settings/customer-tokens' }];

    const copy = () => {
        if (!created_token) return;
        void navigator.clipboard.writeText(created_token.plain_text);
        setCopied(true);
    };

    return (
        <AppLayout breadcrumbs={breadcrumbs}>
            <Head title={t('Customer tokens')} />

            <SettingsLayout>
                <div className="space-y-6">
                    <div className="flex items-start justify-between gap-4">
                        <HeadingSmall
                            title={t('Customer tokens')}
                            description={t(
                                'Tokens you mint for customers to upload through the API. Each is scoped to the permissions you grant it, and one can never mint tokens of its own.',
                            )}
                        />
                        <Button asChild size="sm">
                            <Link href={route('customer-tokens.create')}>{t('Create token')}</Link>
                        </Button>
                    </div>

                    {created_token && (
                        <Alert>
                            <AlertTitle>{t('Copy your token now')}</AlertTitle>
                            <AlertDescription className="space-y-3">
                                <p>{t('This is the only time it will be shown. We store only a hash, so it cannot be recovered later.')}</p>
                                <code className="bg-muted block w-full rounded p-2 font-mono text-xs break-all">{created_token.plain_text}</code>
                                <Button type="button" size="sm" variant="outline" onClick={copy}>
                                    {copied ? t('Copied') : t('Copy to clipboard')}
                                </Button>
                            </AlertDescription>
                        </Alert>
                    )}

                    {tokens.length === 0 ? (
                        <p className="text-muted-foreground text-sm">{t('You have not minted any customer tokens yet.')}</p>
                    ) : (
                        <ul className="divide-border divide-y">
                            {tokens.map((token) => (
                                <li key={token.id} className="flex items-start justify-between gap-4 py-3">
                                    <div className="space-y-1">
                                        <div className="flex items-center gap-2">
                                            <span className="text-sm font-medium">{token.name}</span>
                                            {token.expired && <Badge variant="destructive">{t('Expired')}</Badge>}
                                        </div>
                                        <p className="text-muted-foreground text-xs">
                                            {token.last_used_at ? t('Last used :date', { date: date(token.last_used_at) }) : t('Never used')}
                                            {' · '}
                                            {token.expires_at ? t('Expires :date', { date: date(token.expires_at) }) : t('Never expires')}
                                        </p>
                                        <p className="text-muted-foreground text-xs">{token.abilities.join(', ')}</p>
                                    </div>

                                    <div className="flex shrink-0 items-center gap-2">
                                        <ConfirmDialog
                                            trigger={
                                                <Button variant="outline" size="sm">
                                                    {t('Revoke')}
                                                </Button>
                                            }
                                            title={t('Revoke this token?')}
                                            description={t('Any customer using it will stop working immediately. This cannot be undone.')}
                                            confirmLabel={t('Revoke')}
                                            onConfirm={() => router.delete(route('customer-tokens.destroy', token.id), { preserveScroll: true })}
                                        />
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </SettingsLayout>
        </AppLayout>
    );
}
