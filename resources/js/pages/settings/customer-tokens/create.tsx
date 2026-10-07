import { Head, Link, useForm } from '@inertiajs/react';
import { FormEventHandler } from 'react';

import Heading from '@/components/heading';
import InputError from '@/components/input-error';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useTranslation } from '@/hooks/use-translation';
import AppLayout from '@/layouts/app-layout';
import { type BreadcrumbItem } from '@/types';

interface FolderOption {
    id: number;
    name: string;
    path: string;
}

interface Props {
    folders: FolderOption[];
    defaults: { expires_in_days: number; max_days: number };
}

export default function CustomerTokenCreate({ folders, defaults }: Props) {
    const { t } = useTranslation();

    const breadcrumbs: BreadcrumbItem[] = [
        { title: t('Customer tokens'), href: '/settings/customer-tokens' },
        { title: t('Create token'), href: '/settings/customer-tokens/create' },
    ];

    const { data, setData, post, processing, errors } = useForm({
        name: '',
        folder_id: null as number | null,
        expires_in_days: defaults.expires_in_days,
        never_expires: false,
    });

    const submit: FormEventHandler = (e) => {
        e.preventDefault();
        post(route('customer-tokens.store'));
    };

    return (
        <AppLayout breadcrumbs={breadcrumbs}>
            <Head title={t('Create customer token')} />

            <div className="space-y-8 px-4 py-6">
                <Heading
                    title={t('Create customer token')}
                    description={t('The token is upload-only and bound to the folder you choose — it can only push files into that folder. It is shown once, immediately after it is created.')}
                />

                <form onSubmit={submit} className="max-w-xl space-y-6">
                    <div className="grid gap-2">
                        <Label htmlFor="name">{t('Token name')}</Label>
                        <Input
                            id="name"
                            value={data.name}
                            onChange={(e) => setData('name', e.target.value)}
                            placeholder={t('e.g. Acme Corp diagnostics')}
                            autoComplete="off"
                        />
                        <p className="text-muted-foreground text-sm">
                            {t('Name it after the customer, so you know what you are revoking later.')}
                        </p>
                        <InputError message={errors.name} />
                    </div>

                    <div className="grid gap-2">
                        <Label>{t('Folder')}</Label>
                        <Select value={data.folder_id ? String(data.folder_id) : undefined} onValueChange={(value) => setData('folder_id', Number(value))}>
                            <SelectTrigger>
                                <SelectValue placeholder={t('Select the folder this token may upload into')} />
                            </SelectTrigger>
                            <SelectContent>
                                {folders.map((folder) => (
                                    <SelectItem key={folder.id} value={String(folder.id)}>
                                        {folder.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-muted-foreground text-sm">
                            {t('Every file this token uploads lands in this folder, and nowhere else.')}
                        </p>
                        <InputError message={errors.folder_id} />
                    </div>

                    <div className="grid gap-2">
                        <Label htmlFor="expires_in_days">{t('Expires in (days)')}</Label>
                        <Input
                            id="expires_in_days"
                            type="number"
                            min={1}
                            max={defaults.max_days}
                            value={data.expires_in_days}
                            disabled={data.never_expires}
                            onChange={(e) => setData('expires_in_days', Number(e.target.value))}
                            className="max-w-32"
                        />
                        <div className="flex items-center gap-2">
                            <Checkbox
                                id="never_expires"
                                checked={data.never_expires}
                                onCheckedChange={(checked) => setData('never_expires', checked === true)}
                            />
                            <Label htmlFor="never_expires" className="text-sm font-normal">
                                {t('Never expires')}
                            </Label>
                        </div>
                        {data.never_expires && (
                            <p className="text-sm text-amber-600 dark:text-amber-500">
                                {t('A token that never expires stays valid until someone revokes it by hand. Prefer a date you can forget about safely.')}
                            </p>
                        )}
                        <InputError message={errors.expires_in_days} />
                    </div>

                    <div className="flex items-center gap-3">
                        <Button type="submit" disabled={processing || data.folder_id === null}>
                            {t('Create token')}
                        </Button>
                        <Button type="button" variant="ghost" asChild>
                            <Link href={route('customer-tokens.index')}>{t('Cancel')}</Link>
                        </Button>
                    </div>
                </form>
            </div>
        </AppLayout>
    );
}
