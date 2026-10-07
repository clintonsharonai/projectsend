import { Head, Link, router } from '@inertiajs/react';
import { useState } from 'react';

import { ConfirmDialog } from '@/components/confirm-dialog';
import Heading from '@/components/heading';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useFormatDate } from '@/hooks/use-format-date';
import { useTranslation } from '@/hooks/use-translation';
import AppLayout from '@/layouts/app-layout';
import { type BreadcrumbItem } from '@/types';

interface CustomerToken {
    id: string;
    name: string;
    folder_id: number | null;
    folder_name: string | null;
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

            <div className="space-y-8 px-4 py-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <Heading
                        title={t('Customer tokens')}
                        description={t('Upload-only tokens, each bound to a single folder. A customer with the token can only push files into that folder.')}
                    />
                    <Button size="sm" asChild>
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
                    <div className="overflow-x-auto rounded-lg border">
                        <table className="w-full text-sm">
                            <thead className="bg-muted/50 text-left">
                                <tr>
                                    <th className="px-4 py-2 font-medium">{t('Name')}</th>
                                    <th className="px-4 py-2 font-medium">{t('Folder')}</th>
                                    <th className="px-4 py-2 font-medium">{t('Last used')}</th>
                                    <th className="px-4 py-2 font-medium">{t('Expires')}</th>
                                    <th className="px-4 py-2" />
                                </tr>
                            </thead>
                            <tbody>
                                {tokens.map((token) => (
                                    <tr key={token.id} className="border-t">
                                        <td className="px-4 py-2">
                                            <div className="flex items-center gap-2">
                                                <span className="font-medium">{token.name}</span>
                                                {token.expired && <Badge variant="destructive">{t('Expired')}</Badge>}
                                            </div>
                                        </td>
                                        <td className="text-muted-foreground px-4 py-2">{token.folder_name ?? '—'}</td>
                                        <td className="text-muted-foreground px-4 py-2 whitespace-nowrap">
                                            {token.last_used_at ? date(token.last_used_at) : t('Never')}
                                        </td>
                                        <td className="text-muted-foreground px-4 py-2 whitespace-nowrap">
                                            {token.expires_at ? date(token.expires_at) : t('Never expires')}
                                        </td>
                                        <td className="px-4 py-2 text-right">
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
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </AppLayout>
    );
}
